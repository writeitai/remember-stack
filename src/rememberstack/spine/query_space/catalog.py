"""The declared half of the `memory_v1` schema contract.

Four facts about a public view cannot be derived from the authored DDL and must
therefore be declared here, next to each other, where a reviewer can see them:

- **Column types.** Resolving the type of a view's output expression needs a
  PostgreSQL type system, and the manifest must be computable without a running
  server. The types are therefore declared below in the published column order
  and *proven* by the schema gate, which reads the deployed types from
  `pg_catalog.format_type` alone and compares them to these declarations. The
  live side of that comparison never reads this module, so the check cannot
  pass by comparing a declaration with itself.
- **Nullability.** PostgreSQL does not track whether a view column can be null;
  `pg_attribute.attnotnull` is false for every view column regardless of the
  expression behind it. There is no catalog source to compare against, so the
  contract's nullability is declared below and proven by execution: the schema
  gate asserts that no column declared non-null is ever null across the fixture
  corpus.
- **Row key and join keys.** A view has no primary key. The declared row key is
  the tuple a caller may assume identifies one row, and the gate proves it is
  unique on fixtures; the join keys name where each foreign column resolves.
- **Grain, clock semantics, and finite vocabularies.** The grain phrase says
  what one row *is*, the clock tag says which time axes the row's instants
  belong to, and an enum vocabulary is bound here because the views expose
  finite vocabularies as `text` rather than leaking private PostgreSQL enum
  type names into the public surface.

The rest of the manifest — the ordered column names, the relation and column
comments, and the canonical definition AST — is read from the authored DDL in
the migration (`source_definitions.py`), which is what the deployed schema is
built from, and is likewise compared against live introspection by the gate.
"""

from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict

#: Major version of the query space this module describes.
QUERY_SPACE_SCHEMA: Final = "memory_v1"
QUERY_SPACE_SCHEMA_MAJOR: Final = 1

#: The PostgreSQL major this contract is written against and deployed on. It is
#: a manifest field and therefore a hash input; the minor version deliberately
#: is not, because the surface must not change when the server is patched. The
#: schema gate asserts the running server reports this major.
POSTGRESQL_MAJOR: Final = 19

#: Finite vocabularies shared by several views, bound once.
_DOCUMENT_STATUS: Final = (
    "ingesting",
    "converting",
    "structuring",
    "ready",
    "failed",
    "deleted",
)
_CLAIM_VALID_PRECISION: Final = (
    "unknown",
    "instant",
    "day",
    "month",
    "quarter",
    "year",
    "open",
)
_CLAIM_VALID_KIND: Final = (
    "proposition_validity",
    "event_time",
    "measurement_period",
    "effective_period",
)
_CLAIM_TEMPORAL_CLASS: Final = ("static", "dynamic", "atemporal")
_GROUNDING_AUDIT_STATUS: Final = (
    "unaudited",
    "sampled_pass",
    "sampled_fail",
    "escalated",
)
_SECTION_ROLE: Final = (
    "body",
    "abstract",
    "introduction",
    "results",
    "methods",
    "discussion",
    "conclusion",
    "references",
    "appendix",
    "table",
    "figure_caption",
    "nav",
    "boilerplate",
    "legal",
)
_FACT_KIND: Final = ("relation", "observation")
_STANCE: Final = ("supports", "contradicts")
_SUPPORT_STATE: Final = ("current", "withdrawn")
#: Decision tiers that can actually appear on a verdict; T1/T2 are blocking
#: tiers and a database CHECK forbids them as a decision method.
_DECISION_METHOD: Final = ("T0", "T3", "T4_small", "T4_frontier", "human")


class JoinKey(BaseModel):
    """One documented path from this view's columns to another relation."""

    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]
    target: str


class ViewContract(BaseModel):
    """The declared contract of one `memory_v1` relation."""

    model_config = ConfigDict(frozen=True)

    name: str
    grain: str
    """The row grain, in the binding design's own words."""

    grain_tag: str
    """Machine-readable grain label a result header reports for this relation."""

    clock_semantics: str
    """Which time axes this view's instants belong to."""

    row_key: tuple[str, ...]
    join_keys: tuple[JoinKey, ...]
    column_types: dict[str, str]
    """Each published column's canonical `format_type` name, in column order."""

    not_null: frozenset[str]
    """Columns a caller may rely on never being null."""

    enum_values: dict[str, tuple[str, ...]]
    """Bound vocabularies for the finite-vocabulary text columns."""

    indexes_used: tuple[str, ...]
    """Existing indexes the definition's authorization chain relies on."""

    positive_fixture: str
    """A case that MUST produce a row, proven by the schema gate."""

    negative_fixture: str
    """A case that MUST NOT produce a row, proven by the schema gate."""


VIEW_CONTRACTS: Final = (
    ViewContract(
        name="documents_live",
        grain="one live document lineage",
        grain_tag="document_lineage_live",
        clock_semantics="source_observation_instants",
        row_key=("deployment_id", "doc_id"),
        join_keys=(),
        not_null=frozenset(
            {
                "deployment_id",
                "doc_id",
                "source_kind",
                "versioning_mode",
                "origin",
                "first_seen_at",
                "has_current_ready_content",
            }
        ),
        enum_values={
            "versioning_mode": ("snapshot", "living"),
            "origin": ("external", "system_generated"),
            "current_version_status": _DOCUMENT_STATUS,
        },
        column_types={
            "deployment_id": "uuid",
            "doc_id": "uuid",
            "source_kind": "text",
            "source_ref": "text",
            "source_uri": "text",
            "title": "text",
            "versioning_mode": "text",
            "origin": "text",
            "first_seen_at": "timestamp with time zone",
            "last_observed_at": "timestamp with time zone",
            "current_version_id": "uuid",
            "current_version_no": "integer",
            "current_version_status": "text",
            "current_representation_id": "uuid",
            "has_current_ready_content": "boolean",
            "source_modified_at": "timestamp with time zone",
            "published_at": "timestamp with time zone",
            "language": "text",
        },
        indexes_used=("documents_pkey", "ix_documents_live", "document_versions_pkey"),
        positive_fixture="documents_live.live_lineage_present",
        negative_fixture="documents_live.tombstoned_lineage_absent",
    ),
    ViewContract(
        name="document_versions_visible",
        grain="one visible version of a live lineage",
        grain_tag="document_version_visible",
        clock_semantics="ingest_and_supersession_instants",
        row_key=("deployment_id", "version_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "version_id",
                "doc_id",
                "version_no",
                "content_hash",
                "status",
                "ingested_at",
                "is_current_version",
            }
        ),
        enum_values={"status": _DOCUMENT_STATUS},
        column_types={
            "deployment_id": "uuid",
            "version_id": "uuid",
            "doc_id": "uuid",
            "version_no": "integer",
            "content_hash": "text",
            "source_version_ref": "text",
            "status": "text",
            "current_representation_id": "uuid",
            "ingested_at": "timestamp with time zone",
            "source_modified_at": "timestamp with time zone",
            "published_at": "timestamp with time zone",
            "language": "text",
            "superseded_at": "timestamp with time zone",
            "is_current_version": "boolean",
        },
        indexes_used=("document_versions_pkey", "documents_pkey"),
        positive_fixture="document_versions_visible.live_version_present",
        negative_fixture="document_versions_visible.tombstoned_version_absent",
    ),
    ViewContract(
        name="sections_live",
        grain="one section in a current ready representation",
        grain_tag="section_current_content",
        clock_semantics="none",
        row_key=("deployment_id", "section_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
            JoinKey(
                columns=("deployment_id", "version_id"),
                target="memory_v1.document_versions_visible",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "section_id",
                "doc_id",
                "version_id",
                "representation_id",
                "structure_generation_id",
                "node_path",
                "normalized_title",
                "role",
                "ordinal",
                "block_start",
                "block_end",
                "char_start",
                "char_end",
            }
        ),
        enum_values={"role": _SECTION_ROLE},
        column_types={
            "deployment_id": "uuid",
            "section_id": "uuid",
            "doc_id": "uuid",
            "version_id": "uuid",
            "representation_id": "uuid",
            "structure_generation_id": "uuid",
            "parent_section_id": "uuid",
            "node_path": "text",
            "heading_level": "smallint",
            "title": "text",
            "normalized_title": "text",
            "role": "text",
            "ordinal": "integer",
            "block_start": "integer",
            "block_end": "integer",
            "char_start": "integer",
            "char_end": "integer",
            "page_start": "integer",
            "page_end": "integer",
            "summary": "text",
        },
        indexes_used=("ix_sections_doc", "uq_sections_generation_path"),
        positive_fixture="sections_live.current_representation_section_present",
        negative_fixture="sections_live.superseded_generation_section_absent",
    ),
    ViewContract(
        name="chunks_live",
        grain="one chunk coordinate in a current ready representation",
        grain_tag="chunk_current_content",
        clock_semantics="none",
        row_key=("deployment_id", "chunk_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
            JoinKey(
                columns=("deployment_id", "version_id"),
                target="memory_v1.document_versions_visible",
            ),
            JoinKey(
                columns=("deployment_id", "section_id"),
                target="memory_v1.sections_live",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "chunk_id",
                "doc_id",
                "version_id",
                "representation_id",
                "ordinal",
                "block_start",
                "block_end",
                "char_start",
                "char_end",
                "chunk_content_hash",
                "extraction_input_hash",
                "created_at",
            }
        ),
        enum_values={},
        column_types={
            "deployment_id": "uuid",
            "chunk_id": "uuid",
            "doc_id": "uuid",
            "version_id": "uuid",
            "representation_id": "uuid",
            "section_id": "uuid",
            "ordinal": "integer",
            "block_start": "integer",
            "block_end": "integer",
            "char_start": "integer",
            "char_end": "integer",
            "token_count": "integer",
            "chunk_content_hash": "text",
            "extraction_input_hash": "text",
            "embedding_text_hash": "text",
            "location_facts": "jsonb",
            "location_header": "text",
            "embedding_input_policy_version": "text",
            "policy_generation": "text",
            "embedder_generation": "text",
            "chunker_version": "text",
            "prefixer_version": "text",
            "created_at": "timestamp with time zone",
        },
        indexes_used=("ix_chunks_doc", "ix_chunks_version", "ix_chunks_section"),
        positive_fixture="chunks_live.current_representation_chunk_present",
        negative_fixture="chunks_live.superseded_version_chunk_absent",
    ),
    ViewContract(
        name="claims_visible_history",
        grain="one historically visible claim with surviving lineage",
        grain_tag="claim_visible_history",
        clock_semantics="claim_validity_immutable",
        row_key=("deployment_id", "claim_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
            JoinKey(
                columns=("deployment_id", "version_id"),
                target="memory_v1.document_versions_visible",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "claim_id",
                "doc_id",
                "version_id",
                "representation_id",
                "chunk_id",
                "claim_text",
                "source_span",
                "char_start",
                "char_end",
                "added_context",
                "is_attributed",
                "audit_status",
                "kept_flagged",
                "extractor_version",
                "claim_valid_precision",
                "ingested_at",
                "source_kind",
                "source_handle",
                "is_current_testimony",
            }
        ),
        enum_values={
            "temporal_class": _CLAIM_TEMPORAL_CLASS,
            "audit_status": _GROUNDING_AUDIT_STATUS,
            "claim_valid_precision": _CLAIM_VALID_PRECISION,
            "claim_valid_kind": _CLAIM_VALID_KIND,
        },
        column_types={
            "deployment_id": "uuid",
            "claim_id": "uuid",
            "doc_id": "uuid",
            "version_id": "uuid",
            "representation_id": "uuid",
            "chunk_id": "uuid",
            "claim_text": "text",
            "source_span": "text",
            "char_start": "integer",
            "char_end": "integer",
            "added_context": "jsonb",
            "temporal_class": "text",
            "is_attributed": "boolean",
            "audit_status": "text",
            "kept_flagged": "boolean",
            "extractor_version": "text",
            "asserted_at": "timestamp with time zone",
            "claim_valid_from": "timestamp with time zone",
            "claim_valid_until": "timestamp with time zone",
            "claim_valid_precision": "text",
            "claim_valid_kind": "text",
            "ingested_at": "timestamp with time zone",
            "source_kind": "text",
            "source_handle": "text",
            "is_current_testimony": "boolean",
        },
        indexes_used=("ix_claims_doc", "ix_claims_chunk", "ix_claims_valid_window"),
        positive_fixture="claims_visible_history.superseded_testimony_present",
        negative_fixture="claims_visible_history.forgotten_lineage_claim_absent",
    ),
    ViewContract(
        name="claims_live",
        grain="one current-testimony claim",
        grain_tag="claim_current_testimony",
        clock_semantics="claim_validity_immutable",
        row_key=("deployment_id", "claim_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
            JoinKey(
                columns=("deployment_id", "version_id"),
                target="memory_v1.document_versions_visible",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "claim_id",
                "doc_id",
                "version_id",
                "representation_id",
                "chunk_id",
                "claim_text",
                "source_span",
                "char_start",
                "char_end",
                "added_context",
                "is_attributed",
                "audit_status",
                "kept_flagged",
                "extractor_version",
                "claim_valid_precision",
                "ingested_at",
                "source_kind",
                "source_handle",
            }
        ),
        enum_values={
            "temporal_class": _CLAIM_TEMPORAL_CLASS,
            "audit_status": _GROUNDING_AUDIT_STATUS,
            "claim_valid_precision": _CLAIM_VALID_PRECISION,
            "claim_valid_kind": _CLAIM_VALID_KIND,
        },
        column_types={
            "deployment_id": "uuid",
            "claim_id": "uuid",
            "doc_id": "uuid",
            "version_id": "uuid",
            "representation_id": "uuid",
            "chunk_id": "uuid",
            "claim_text": "text",
            "source_span": "text",
            "char_start": "integer",
            "char_end": "integer",
            "added_context": "jsonb",
            "temporal_class": "text",
            "is_attributed": "boolean",
            "audit_status": "text",
            "kept_flagged": "boolean",
            "extractor_version": "text",
            "asserted_at": "timestamp with time zone",
            "claim_valid_from": "timestamp with time zone",
            "claim_valid_until": "timestamp with time zone",
            "claim_valid_precision": "text",
            "claim_valid_kind": "text",
            "ingested_at": "timestamp with time zone",
            "source_kind": "text",
            "source_handle": "text",
        },
        indexes_used=("ix_claims_current", "ix_claims_doc", "ix_claims_chunk"),
        positive_fixture="claims_live.current_testimony_present",
        negative_fixture="claims_live.superseded_testimony_absent",
    ),
    ViewContract(
        name="claim_occurrences_live",
        grain="one current claim occurrence",
        grain_tag="claim_occurrence_current_content",
        clock_semantics="none",
        row_key=("deployment_id", "claim_id", "chunk_id", "derivation_kind"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "claim_id"),
                target="memory_v1.claims_visible_history",
            ),
            JoinKey(
                columns=("deployment_id", "chunk_id"), target="memory_v1.chunks_live"
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "claim_id",
                "chunk_id",
                "doc_id",
                "version_id",
                "representation_id",
                "attached_at",
            }
        ),
        enum_values={},
        column_types={
            "deployment_id": "uuid",
            "claim_id": "uuid",
            "chunk_id": "uuid",
            "derivation_kind": "text",
            "doc_id": "uuid",
            "version_id": "uuid",
            "representation_id": "uuid",
            "section_id": "uuid",
            "evidence_mode": "text",
            "source_locators": "jsonb",
            "attached_at": "timestamp with time zone",
        },
        indexes_used=("ix_chunkclaims_claim", "chunk_claims_pkey"),
        positive_fixture="claim_occurrences_live.current_chunk_occurrence_present",
        negative_fixture="claim_occurrences_live.repeated_attachment_collapsed",
    ),
    ViewContract(
        name="testimony_currency_events_visible",
        grain="one visible D54 transition",
        grain_tag="testimony_currency_event_visible",
        clock_semantics="transaction_time_event",
        row_key=("deployment_id", "event_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
            JoinKey(
                columns=("deployment_id", "claim_id"),
                target="memory_v1.claims_visible_history",
            ),
            JoinKey(
                columns=("deployment_id", "from_version_id"),
                target="memory_v1.document_versions_visible",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "event_id",
                "claim_id",
                "doc_id",
                "reconciliation_id",
                "became_current",
                "reason",
                "occurred_at",
            }
        ),
        enum_values={
            "reason": (
                "reextracted",
                "version_superseded",
                "version_deleted",
                "review_restored",
            )
        },
        column_types={
            "deployment_id": "uuid",
            "event_id": "uuid",
            "claim_id": "uuid",
            "doc_id": "uuid",
            "reconciliation_id": "uuid",
            "became_current": "boolean",
            "reason": "text",
            "from_extractor_version": "text",
            "from_version_id": "uuid",
            "occurred_at": "timestamp with time zone",
        },
        indexes_used=("ix_currency_claim", "ix_currency_doc"),
        positive_fixture="testimony_currency_events_visible.reextraction_transition_present",
        negative_fixture="testimony_currency_events_visible.forgotten_lineage_transition_absent",
    ),
    ViewContract(
        name="entity_document_mentions",
        grain="one survivor entity × live document",
        grain_tag="entity_document_mention_live",
        clock_semantics="mention_observation_instants",
        row_key=("deployment_id", "entity_id", "doc_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "entity_id"),
                target="memory_v1.entities_current",
            ),
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "entity_id",
                "doc_id",
                "mention_count",
                "first_mentioned_at",
                "last_mentioned_at",
            }
        ),
        enum_values={},
        column_types={
            "deployment_id": "uuid",
            "entity_id": "uuid",
            "doc_id": "uuid",
            "mention_count": "bigint",
            "first_mentioned_at": "timestamp with time zone",
            "last_mentioned_at": "timestamp with time zone",
        },
        indexes_used=(
            "ix_mentions_doc",
            "ix_chunks_doc",
            "ix_chunks_version",
            "ix_resdec_mention",
            "ix_resdec_live",
            "entities_pkey",
        ),
        positive_fixture="entity_document_mentions.exact_current_content_count_present",
        negative_fixture="entity_document_mentions.forgotten_lineage_pair_absent",
    ),
    ViewContract(
        name="entities_current",
        grain="one externally visible survivor entity",
        grain_tag="entity_survivor_current",
        clock_semantics="registry_maintenance_instants",
        row_key=("deployment_id", "entity_id"),
        join_keys=(),
        not_null=frozenset(
            {
                "deployment_id",
                "entity_id",
                "canonical_name",
                "normalized_name",
                "live_mention_count",
                "live_document_count",
                "graph_degree",
                "created_at",
                "updated_at",
            }
        ),
        enum_values={},
        column_types={
            "deployment_id": "uuid",
            "entity_id": "uuid",
            "entity_type": "text",
            "canonical_name": "text",
            "normalized_name": "text",
            "type_confidence": "real",
            "profile_summary": "text",
            "live_mention_count": "bigint",
            "live_document_count": "bigint",
            "graph_degree": "bigint",
            "created_at": "timestamp with time zone",
            "updated_at": "timestamp with time zone",
        },
        indexes_used=("entities_pkey",),
        positive_fixture="entities_current.survivor_with_live_provenance_present",
        negative_fixture="entities_current.merged_entity_absent",
    ),
    ViewContract(
        name="entity_aliases_current",
        grain="one current alias-to-survivor mapping",
        grain_tag="entity_alias_current",
        clock_semantics="observation_instants",
        row_key=("deployment_id", "alias_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "entity_id"),
                target="memory_v1.entities_current",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "alias_id",
                "source_entity_id",
                "entity_id",
                "alias_text",
                "normalized_lemma",
                "provenance",
                "first_seen",
                "last_seen",
            }
        ),
        enum_values={"provenance": ("source", "llm_canonical")},
        column_types={
            "deployment_id": "uuid",
            "alias_id": "uuid",
            "source_entity_id": "uuid",
            "entity_id": "uuid",
            "alias_text": "text",
            "normalized_lemma": "text",
            "provenance": "text",
            "confidence": "real",
            "first_seen": "timestamp with time zone",
            "last_seen": "timestamp with time zone",
        },
        indexes_used=("ix_aliases_entity", "ix_aliases_lemma_exact"),
        positive_fixture="entity_aliases_current.alias_redirected_to_survivor",
        negative_fixture="entity_aliases_current.forgotten_entity_alias_absent",
    ),
    ViewContract(
        name="mentions_live",
        grain="one mention in current content",
        grain_tag="mention_current_content",
        clock_semantics="none",
        row_key=("deployment_id", "mention_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
            JoinKey(
                columns=("deployment_id", "chunk_id"), target="memory_v1.chunks_live"
            ),
            JoinKey(
                columns=("deployment_id", "resolved_entity_id"),
                target="memory_v1.entities_current",
            ),
            JoinKey(
                columns=("deployment_id", "claim_id"),
                target="memory_v1.claims_visible_history",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "mention_id",
                "doc_id",
                "version_id",
                "representation_id",
                "chunk_id",
                "surface_form",
                "normalized_lemma",
                "created_at",
            }
        ),
        enum_values={"resolution_method": _DECISION_METHOD},
        column_types={
            "deployment_id": "uuid",
            "mention_id": "uuid",
            "doc_id": "uuid",
            "version_id": "uuid",
            "representation_id": "uuid",
            "chunk_id": "uuid",
            "section_id": "uuid",
            "claim_id": "uuid",
            "surface_form": "text",
            "normalized_lemma": "text",
            "canonical_name_form": "text",
            "emitted_type": "text",
            "type_confidence": "real",
            "language": "text",
            "char_start": "integer",
            "char_end": "integer",
            "created_at": "timestamp with time zone",
            "resolved_entity_id": "uuid",
            "resolution_method": "text",
            "resolution_confidence": "real",
            "resolution_is_new_entity": "boolean",
            "resolved_at": "timestamp with time zone",
        },
        indexes_used=(
            "ix_mentions_doc",
            "ix_chunks_doc",
            "ix_resdec_mention",
            "ix_resdec_live",
            "entities_pkey",
        ),
        positive_fixture="mentions_live.unresolved_mention_present",
        negative_fixture="mentions_live.superseded_version_mention_absent",
    ),
    ViewContract(
        name="identity_events_visible",
        grain="one visible resolution/merge/split event",
        grain_tag="identity_event_visible",
        clock_semantics="transaction_time_event",
        row_key=("deployment_id", "object_kind", "event_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "entity_id"),
                target="memory_v1.entities_current",
            ),
            JoinKey(
                columns=("deployment_id", "mention_id"),
                target="memory_v1.mentions_live",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "object_kind",
                "event_id",
                "entity_id",
                "outcome",
                "method",
                "decided_by",
                "decided_at",
                "is_superseded",
            }
        ),
        enum_values={
            "object_kind": ("resolution_decision", "merge_event"),
            "outcome": ("linked", "new_entity", "merge", "unmerge"),
            "method": (*_DECISION_METHOD, "merge_event"),
            "decided_by": ("auto", "human"),
        },
        column_types={
            "deployment_id": "uuid",
            "object_kind": "text",
            "event_id": "uuid",
            "entity_id": "uuid",
            "related_entity_id": "uuid",
            "mention_id": "uuid",
            "outcome": "text",
            "method": "text",
            "confidence": "real",
            "decided_by": "text",
            "decided_at": "timestamp with time zone",
            "is_superseded": "boolean",
        },
        indexes_used=("ix_resdec_entity", "ix_merge_survivor", "ix_merge_absorbed"),
        positive_fixture="identity_events_visible.resolution_and_merge_arms_present",
        negative_fixture="identity_events_visible.forgotten_source_event_absent",
    ),
    ViewContract(
        name="fact_claim_evidence_live",
        grain="one current claim-to-fact association",
        grain_tag="fact_claim_evidence_live",
        clock_semantics="claim_validity_immutable",
        row_key=("deployment_id", "fact_kind", "fact_id", "claim_id", "stance"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "fact_kind", "fact_id"),
                target="memory_v1.facts_visible_history",
            ),
            JoinKey(
                columns=("deployment_id", "claim_id"), target="memory_v1.claims_live"
            ),
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "fact_kind",
                "fact_id",
                "claim_id",
                "stance",
                "doc_id",
                "source_kind",
                "source_handle",
                "claim_valid_precision",
                "linked_at",
            }
        ),
        enum_values={
            "fact_kind": _FACT_KIND,
            "stance": _STANCE,
            "claim_valid_precision": _CLAIM_VALID_PRECISION,
            "claim_valid_kind": _CLAIM_VALID_KIND,
        },
        column_types={
            "deployment_id": "uuid",
            "fact_kind": "text",
            "fact_id": "uuid",
            "claim_id": "uuid",
            "stance": "text",
            "doc_id": "uuid",
            "source_kind": "text",
            "source_handle": "text",
            "asserted_at": "timestamp with time zone",
            "claim_valid_from": "timestamp with time zone",
            "claim_valid_until": "timestamp with time zone",
            "claim_valid_precision": "text",
            "claim_valid_kind": "text",
            "linked_at": "timestamp with time zone",
        },
        indexes_used=(
            "relation_evidence_pkey",
            "observation_evidence_pkey",
            "ix_relevidence_claim",
            "ix_obsevidence_claim",
        ),
        positive_fixture="fact_claim_evidence_live.both_stances_present",
        negative_fixture="fact_claim_evidence_live.superseded_testimony_absent",
    ),
    ViewContract(
        name="evidence_lineage",
        grain="one fact × current-testimony document lineage × stance",
        grain_tag="evidence_lineage",
        clock_semantics="assertion_event_range",
        row_key=("deployment_id", "fact_kind", "fact_id", "doc_id", "stance"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "fact_kind", "fact_id"),
                target="memory_v1.facts_visible_history",
            ),
            JoinKey(
                columns=("deployment_id", "doc_id"), target="memory_v1.documents_live"
            ),
            JoinKey(
                columns=("deployment_id", "representative_claim_id"),
                target="memory_v1.claims_live",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "fact_kind",
                "fact_id",
                "doc_id",
                "stance",
                "source_kind",
                "source_handle",
                "claim_count",
                "representative_claim_id",
            }
        ),
        enum_values={"fact_kind": _FACT_KIND, "stance": _STANCE},
        column_types={
            "deployment_id": "uuid",
            "fact_kind": "text",
            "fact_id": "uuid",
            "doc_id": "uuid",
            "stance": "text",
            "source_kind": "text",
            "source_handle": "text",
            "claim_count": "bigint",
            "representative_claim_id": "uuid",
            "asserted_from": "timestamp with time zone",
            "asserted_to": "timestamp with time zone",
        },
        indexes_used=("relation_evidence_pkey", "observation_evidence_pkey"),
        positive_fixture="evidence_lineage.repetition_does_not_add_a_lineage",
        negative_fixture="evidence_lineage.forgotten_lineage_evidence_absent",
    ),
    ViewContract(
        name="facts_visible_history",
        grain="one historically visible relation or observation",
        grain_tag="fact_visible_history",
        clock_semantics="bitemporal_raw",
        row_key=("deployment_id", "fact_kind", "fact_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "subject_entity_id"),
                target="memory_v1.entities_current",
            ),
            JoinKey(
                columns=("deployment_id", "object_entity_id"),
                target="memory_v1.entities_current",
            ),
            JoinKey(
                columns=("deployment_id", "fact_kind", "fact_id"),
                target="memory_v1.evidence_lineage",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "fact_kind",
                "fact_id",
                "subject_entity_id",
                "ingested_at",
                "evidence_count_current",
                "contradict_count_current",
                "support_state_current",
            }
        ),
        enum_values={"fact_kind": _FACT_KIND, "support_state_current": _SUPPORT_STATE},
        column_types={
            "deployment_id": "uuid",
            "fact_kind": "text",
            "fact_id": "uuid",
            "subject_entity_id": "uuid",
            "predicate": "text",
            "object_entity_id": "uuid",
            "statement": "text",
            "fact_label": "text",
            "valid_from": "timestamp with time zone",
            "valid_until": "timestamp with time zone",
            "ingested_at": "timestamp with time zone",
            "invalidated_at": "timestamp with time zone",
            "contradiction_group": "uuid",
            "confidence": "real",
            "evidence_count_current": "bigint",
            "contradict_count_current": "bigint",
            "support_state_current": "text",
        },
        indexes_used=(
            "relations_pkey",
            "observations_pkey",
            "relation_evidence_pkey",
            "observation_evidence_pkey",
            "ix_review_pending",
        ),
        positive_fixture="facts_visible_history.invalidated_fact_present",
        negative_fixture="facts_visible_history.forgotten_provenance_fact_absent",
    ),
    ViewContract(
        name="facts_current",
        grain="one currently valid relation or observation",
        grain_tag="fact_current",
        clock_semantics="bitemporal_current_at_evaluated_at",
        row_key=("deployment_id", "fact_kind", "fact_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "subject_entity_id"),
                target="memory_v1.entities_current",
            ),
            JoinKey(
                columns=("deployment_id", "object_entity_id"),
                target="memory_v1.entities_current",
            ),
            JoinKey(
                columns=("deployment_id", "fact_kind", "fact_id"),
                target="memory_v1.fact_claim_evidence_live",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "fact_kind",
                "fact_id",
                "subject_entity_id",
                "ingested_at",
                "evidence_count",
                "contradict_count",
                "support_state",
                "evaluated_at",
            }
        ),
        enum_values={"fact_kind": _FACT_KIND, "support_state": _SUPPORT_STATE},
        column_types={
            "deployment_id": "uuid",
            "fact_kind": "text",
            "fact_id": "uuid",
            "subject_entity_id": "uuid",
            "predicate": "text",
            "object_entity_id": "uuid",
            "statement": "text",
            "fact_label": "text",
            "valid_from": "timestamp with time zone",
            "valid_until": "timestamp with time zone",
            "ingested_at": "timestamp with time zone",
            "contradiction_group": "uuid",
            "confidence": "real",
            "evidence_count": "bigint",
            "contradict_count": "bigint",
            "support_state": "text",
            "evaluated_at": "timestamp with time zone",
        },
        indexes_used=("relations_pkey", "observations_pkey", "ix_review_pending"),
        positive_fixture="facts_current.open_window_fact_present",
        negative_fixture="facts_current.ended_window_fact_absent",
    ),
    ViewContract(
        name="contradiction_members_current",
        grain="one current contradiction-group member",
        grain_tag="contradiction_member_current",
        clock_semantics="bitemporal_current_at_evaluated_at",
        row_key=("deployment_id", "contradiction_group", "fact_kind", "fact_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "fact_kind", "fact_id"),
                target="memory_v1.facts_current",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "contradiction_group",
                "fact_kind",
                "fact_id",
                "ingested_at",
                "evidence_count",
                "contradict_count",
                "support_state",
                "evaluated_at",
            }
        ),
        enum_values={"fact_kind": _FACT_KIND, "support_state": _SUPPORT_STATE},
        column_types={
            "deployment_id": "uuid",
            "contradiction_group": "uuid",
            "fact_kind": "text",
            "fact_id": "uuid",
            "fact_label": "text",
            "valid_from": "timestamp with time zone",
            "valid_until": "timestamp with time zone",
            "ingested_at": "timestamp with time zone",
            "evidence_count": "bigint",
            "contradict_count": "bigint",
            "support_state": "text",
            "evaluated_at": "timestamp with time zone",
        },
        indexes_used=("ix_relations_contradiction", "ix_observations_contradiction"),
        positive_fixture="contradiction_members_current.both_sides_present",
        negative_fixture="contradiction_members_current.ungrouped_fact_absent",
    ),
    ViewContract(
        name="graph_edges_current",
        grain="one current relation edge",
        grain_tag="graph_edge_current",
        clock_semantics="bitemporal_current_at_evaluated_at",
        row_key=("deployment_id", "relation_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "subject_entity_id"),
                target="memory_v1.entities_current",
            ),
            JoinKey(
                columns=("deployment_id", "object_entity_id"),
                target="memory_v1.entities_current",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "relation_id",
                "subject_entity_id",
                "object_entity_id",
                "predicate",
                "ingested_at",
                "evidence_count",
                "contradict_count",
                "support_state",
                "evaluated_at",
            }
        ),
        enum_values={"support_state": _SUPPORT_STATE},
        column_types={
            "deployment_id": "uuid",
            "relation_id": "uuid",
            "subject_entity_id": "uuid",
            "object_entity_id": "uuid",
            "predicate": "text",
            "fact_label": "text",
            "valid_from": "timestamp with time zone",
            "valid_until": "timestamp with time zone",
            "ingested_at": "timestamp with time zone",
            "contradiction_group": "uuid",
            "confidence": "real",
            "evidence_count": "bigint",
            "contradict_count": "bigint",
            "support_state": "text",
            "evaluated_at": "timestamp with time zone",
        },
        indexes_used=(
            "ix_relations_block_subj",
            "ix_relations_block_obj",
            "ix_relations_live",
        ),
        positive_fixture="graph_edges_current.survivor_endpoints_present",
        negative_fixture="graph_edges_current.observation_never_projects",
    ),
    ViewContract(
        name="graph_edges_visible_history",
        grain="one historically visible relation edge",
        grain_tag="graph_edge_visible_history",
        clock_semantics="bitemporal_raw",
        row_key=("deployment_id", "relation_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "subject_entity_id"),
                target="memory_v1.entities_current",
            ),
            JoinKey(
                columns=("deployment_id", "object_entity_id"),
                target="memory_v1.entities_current",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "relation_id",
                "subject_entity_id",
                "object_entity_id",
                "predicate",
                "ingested_at",
                "evidence_count_current",
                "contradict_count_current",
                "support_state_current",
            }
        ),
        enum_values={"support_state_current": _SUPPORT_STATE},
        column_types={
            "deployment_id": "uuid",
            "relation_id": "uuid",
            "subject_entity_id": "uuid",
            "object_entity_id": "uuid",
            "predicate": "text",
            "fact_label": "text",
            "valid_from": "timestamp with time zone",
            "valid_until": "timestamp with time zone",
            "ingested_at": "timestamp with time zone",
            "invalidated_at": "timestamp with time zone",
            "contradiction_group": "uuid",
            "confidence": "real",
            "evidence_count_current": "bigint",
            "contradict_count_current": "bigint",
            "support_state_current": "text",
        },
        indexes_used=("ix_relations_block_subj", "ix_relations_predicate"),
        positive_fixture="graph_edges_visible_history.invalidated_edge_present",
        negative_fixture="graph_edges_visible_history.forgotten_provenance_edge_absent",
    ),
    ViewContract(
        name="document_crossrefs_live",
        grain="one live document cross-reference",
        grain_tag="document_crossref_live",
        clock_semantics="none",
        row_key=("deployment_id", "crossref_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "from_doc_id"),
                target="memory_v1.documents_live",
            ),
            JoinKey(
                columns=("deployment_id", "to_doc_id"),
                target="memory_v1.documents_live",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "crossref_id",
                "from_doc_id",
                "to_doc_id",
                "kind",
                "created_at",
            }
        ),
        enum_values={"kind": ("cites", "links_to", "attaches", "replies_to")},
        column_types={
            "deployment_id": "uuid",
            "crossref_id": "uuid",
            "from_doc_id": "uuid",
            "to_doc_id": "uuid",
            "kind": "text",
            "context": "text",
            "created_at": "timestamp with time zone",
        },
        indexes_used=("ix_crossrefs_from", "ix_crossrefs_to"),
        positive_fixture="document_crossrefs_live.both_endpoints_live_present",
        negative_fixture="document_crossrefs_live.forgotten_target_absent",
    ),
    ViewContract(
        name="pages_live",
        grain="one visible K artifact",
        grain_tag="k_artifact_compiled_grain",
        clock_semantics="compilation_instants",
        row_key=("deployment_id", "artifact_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "parent_artifact_id"),
                target="memory_v1.pages_live",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "artifact_id",
                "layer",
                "page_kind",
                "git_path",
                "status",
                "is_stale",
                "open_review_flags",
                "redaction_required",
            }
        ),
        enum_values={
            "layer": ("K1", "K2", "K3"),
            "page_kind": ("compiled", "authored"),
            "status": ("active", "stale", "quarantined"),
        },
        column_types={
            "deployment_id": "uuid",
            "artifact_id": "uuid",
            "layer": "text",
            "page_kind": "text",
            "git_path": "text",
            "kind": "text",
            "parent_artifact_id": "uuid",
            "page_summary": "text",
            "status": "text",
            "last_compiled_at": "timestamp with time zone",
            "is_stale": "boolean",
            "open_review_flags": "bigint",
            "redaction_required": "boolean",
        },
        indexes_used=(
            "knowledge_artifacts_pkey",
            "ix_kartifacts_stale",
            "ix_krefresh_runnable",
            "ux_kae_link",
            "ix_kae_doc",
            "ix_kae_relation",
        ),
        positive_fixture="pages_live.compiled_page_present",
        negative_fixture="pages_live.tombstoned_and_uncited_pages_absent",
    ),
    ViewContract(
        name="page_evidence_visible",
        grain="one visible K artifact-to-target association",
        grain_tag="k_artifact_evidence_visible",
        clock_semantics="none",
        row_key=("deployment_id", "artifact_id", "role", "target_kind", "target_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "artifact_id"), target="memory_v1.pages_live"
            ),
            JoinKey(
                columns=("deployment_id", "target_id"),
                target="memory_v1.documents_live",
            ),
            JoinKey(
                columns=("deployment_id", "target_id"),
                target="memory_v1.facts_visible_history",
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "artifact_id",
                "role",
                "target_kind",
                "target_id",
                "link_count",
            }
        ),
        enum_values={
            "role": ("supports", "contradicts", "cites"),
            "target_kind": ("claim", "relation", "document"),
        },
        column_types={
            "deployment_id": "uuid",
            "artifact_id": "uuid",
            "role": "text",
            "target_kind": "text",
            "target_id": "uuid",
            "claim_chunk_content_hashes": "text[]",
            "link_count": "bigint",
        },
        indexes_used=(
            "ux_kae_link",
            "ix_kae_claim_coordinate",
            "ix_kae_relation",
            "ix_kae_doc",
        ),
        positive_fixture="page_evidence_visible.claim_coordinate_link_present",
        negative_fixture="page_evidence_visible.forgotten_target_link_absent",
    ),
    ViewContract(
        name="changes_visible",
        grain="one externally visible change event",
        grain_tag="change_event_visible",
        clock_semantics="transaction_time_event",
        row_key=("deployment_id", "object_kind", "event_id"),
        join_keys=(
            JoinKey(
                columns=("deployment_id", "object_id"),
                target="memory_v1.facts_visible_history",
            ),
            JoinKey(
                columns=("deployment_id", "object_id"),
                target="memory_v1.claims_visible_history",
            ),
            JoinKey(
                columns=("deployment_id", "object_id"), target="memory_v1.pages_live"
            ),
        ),
        not_null=frozenset(
            {
                "deployment_id",
                "object_kind",
                "event_id",
                "object_id",
                "occurred_at",
                "label",
            }
        ),
        enum_values={
            "object_kind": (
                "relation_ingest",
                "relation_invalidation",
                "relation_supersession",
                "observation_ingest",
                "observation_invalidation",
                "observation_supersession",
                "claim_ingest",
                "knowledge_page_compilation",
            )
        },
        column_types={
            "deployment_id": "uuid",
            "object_kind": "text",
            "event_id": "uuid",
            "object_id": "uuid",
            "occurred_at": "timestamp with time zone",
            "label": "text",
        },
        indexes_used=(
            "ix_adjud_relation",
            "ix_obsadjud_observation",
            "ix_kcompilations_artifact",
        ),
        positive_fixture="changes_visible.fact_ingest_event_present",
        negative_fixture="changes_visible.forgotten_lineage_change_absent",
    ),
)

VIEW_CONTRACTS_BY_NAME: Final = {contract.name: contract for contract in VIEW_CONTRACTS}
