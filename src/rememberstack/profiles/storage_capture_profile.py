"""Closed current managed writer classification and D55 operator mapping.

These immutable documents select the accepted byte bases, never prices or
caller totals. Their loading/generation is not source/clock/cost qualification;
the operator must independently accept their exact bytes and runtime pins.
Unknown native roots/classes refuse instead of inheriting an inclusion rule.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.adapters.fsit_storage_capture import FilesystemID
from rememberstack.adapters.fsit_storage_capture import ProviderRequest
from rememberstack.model.storage_capture import Digest
from rememberstack.model.storage_capture import StorageCaptureRequest
from rememberstack.model.storage_capture import StorageComponent
from rememberstack.model.storage_capture import StoragePurpose
from rememberstack.model.storage_capture import UUID32
from rememberstack.spine.storage_capture_guard import _ROOTS
from rememberstack.spine.storage_capture_view import NativeCaptureProfile

STORAGE_CAPTURE_PAGE_MEMBER_LIMIT: Final = 1024
"""The current managed canonical member page bound, shared with the receiver."""

_PG_INCLUDED = (
    "aliases",
    "application_context_bindings",
    "assured_operations",
    "chunk_claims",
    "chunk_search",
    "chunks",
    "claim_extraction_decisions",
    "claims",
    "connector_sync_cycles",
    "content_objects",
    "deployment_extension_packs",
    "deployment_processing_layers",
    "deployments",
    "document_crossrefs",
    "document_effective_periods",
    "document_effective_time_events",
    "document_entity_bindings",
    "document_metadata",
    "document_names",
    "document_people",
    "document_reference_generations",
    "document_representations",
    "document_sections",
    "document_skeleton_checks",
    "document_structure_generations",
    "document_version_scope",
    "document_versions",
    "documents",
    "entities",
    "entity_types",
    "extension_packs",
    "fact_applications",
    "ingest_principals",
    "knowledge_artifact_evidence",
    "knowledge_artifacts",
    "knowledge_compilations",
    "knowledge_page_rules",
    "knowledge_page_watches",
    "knowledge_plan_decisions",
    "knowledge_quarantines",
    "knowledge_rule_keys",
    "knowledge_subscriptions",
    "mentions",
    "merge_events",
    "normalization_outputs",
    "observation_adjudications",
    "observation_evidence",
    "observations",
    "p1_search_channels",
    "pipeline_component_versions",
    "predicates",
    "processing_layer_gaps",
    "projection_snapshots",
    "relation_adjudications",
    "relation_evidence",
    "relations",
    "resolution_decisions",
    "resolution_exclusions",
    "resolver_versions",
    "review_queue",
    "saved_queries",
    "saved_query_registry_state",
    "saved_query_versions",
    "scope_interests",
    "scopes",
    "selection_results",
    "testimony_currency_events",
)
_PG_EXCLUDED: dict[StoragePurpose, tuple[str, ...]] = {
    "audit_operations": (
        "canary_cases",
        "cost_ledger",
        "eval_runs",
        "golden_claim_labels",
        "golden_pairs",
        "grounding_audits",
        "knowledge_plan_runs",
        "managed_ingest_measurements",
        "managed_ingest_outcomes",
        "saved_query_audit",
        "surface_cost_ledger",
        "surface_cost_meter_state",
    ),
    "paid_checkpoint": (
        "fact_window_review_attempts",
        "knowledge_dispatches",
        "knowledge_refresh_queue",
        "obs_flush_entity_units",
        "obs_flush_version_state",
        "processing_state",
    ),
    "privacy_control": ("forget_manifests", "perimeter_state"),
    "staging": ("normalize_observation_staging",),
    "adapter_metadata": (
        "alembic_version",
        "part_config",
        "part_config_sub",
        "template_public_chunk_claims",
        "template_public_chunks",
        "template_public_claim_extraction_decisions",
        "template_public_mentions",
        "template_public_resolution_decisions",
        "template_public_surface_cost_ledger",
        "template_public_testimony_currency_events",
    ),
}
_PG_PROJECTIONS = {
    "chunk_search",
    "document_entity_bindings",
    "document_version_scope",
    "p1_search_channels",
}
_OBJECT_RULES: tuple[tuple[str, str, StoragePurpose, StorageComponent | None], ...] = (
    ("content_objects.raw_uri", "raw", "canonical_raw", "canonical_object"),
    (
        "document_representations.markdown_uri",
        "artifacts",
        "referenced_artifact",
        "canonical_object",
    ),
    (
        "document_representations.pageindex_uri",
        "artifacts",
        "referenced_artifact",
        "canonical_object",
    ),
    (
        "document_representations.conversion_uri",
        "artifacts",
        "referenced_artifact",
        "canonical_object",
    ),
    (
        "document_representations.blocks_uri",
        "artifacts",
        "referenced_artifact",
        "canonical_object",
    ),
    (
        "document_representations.meta_uri",
        "artifacts",
        "referenced_artifact",
        "canonical_object",
    ),
    (
        "document_representations.source_map",
        "artifacts",
        "referenced_artifact",
        "canonical_object",
    ),
    (
        "document_representations.derived_asset",
        "artifacts",
        "referenced_artifact",
        "canonical_object",
    ),
    (
        "document_structure_generations.pageindex_uri",
        "artifacts",
        "referenced_artifact",
        "canonical_object",
    ),
    (
        "document_reference_generations.artifact_uri",
        "artifacts",
        "truth_provenance",
        "canonical_object",
    ),
    (
        "knowledge_compilations.session_transcript_uri",
        "artifacts",
        "audit_operations",
        None,
    ),
    (
        "knowledge_plan_runs.session_transcript_uri",
        "artifacts",
        "audit_operations",
        None,
    ),
    ("projection_snapshots.gcs_uri", "corpus", "projection_origin", "p3_origin"),
    ("forget_manifests.portable_intent", "forget", "privacy_control", None),
    ("raw.unregistered_upload", "raw", "staging", None),
    ("raw.unregistered_original", "raw", "staging", None),
    ("artifacts.unregistered_representation", "artifacts", "staging", None),
    ("artifacts.conversion_checkpoint", "artifacts", "paid_checkpoint", None),
    ("artifacts.session_transcript", "artifacts", "audit_operations", None),
    ("corpus.unpublished_snapshot", "corpus", "staging", None),
)


class ProfileRecord(BaseModel):
    """Keep profile documents immutable, closed and explicitly typed."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )


class ManagedFilesystemBinding(ProfileRecord):
    """One actual provider filesystem, never a UUID alias or display prefix."""

    role: Literal["raw", "artifacts", "corpus", "forget"]
    filesystem_id: FilesystemID


class ManagedStoreBindings(ProfileRecord):
    """The exact native database and four distinct role stores for a deployment."""

    contract: Literal["rememberstack.storage_bindings.v1"] = (
        "rememberstack.storage_bindings.v1"
    )
    deployment_id: UUID32
    postgres_database_name: str = Field(min_length=1)
    provider_organisation_id: UUID32
    provider_cell_id: str = Field(min_length=1, max_length=50)
    filesystems: tuple[ManagedFilesystemBinding, ...] = Field(
        min_length=4, max_length=4
    )
    knowledge_repository: Literal["unconfigured"] = "unconfigured"

    def filesystem_for(self, *, role: str) -> str:
        """Resolve an exact role only after validating all four distinct bindings."""
        if tuple(row.role for row in self.filesystems) != (
            "artifacts",
            "corpus",
            "forget",
            "raw",
        ):
            raise ValueError("managed_store_roles")
        ids = tuple(row.filesystem_id for row in self.filesystems)
        ProviderRequest(
            cell_id=self.provider_cell_id,
            cut_at_unix_us="0",
            filesystem_ids=tuple(sorted(ids)),
            organisation_id=self.provider_organisation_id,
            protocol="fsit-capture-v1",
        )
        for row in self.filesystems:
            if row.role == role:
                return row.filesystem_id
        raise ValueError("managed_store_role_unknown")


class ClassificationRule(ProfileRecord):
    """One source-owned purpose for one opaque closed native writer class."""

    native_class_id: Digest
    purpose: StoragePurpose


class StorageClassification(ProfileRecord):
    """The exact D152 classification digest preimage, without commercial terms."""

    contract: Literal["rememberstack.storage_classification.v1"] = (
        "rememberstack.storage_classification.v1"
    )
    source_digest: Digest
    schema_profile_digest: Digest
    rules: tuple[ClassificationRule, ...]


class MappingRule(ProfileRecord):
    """One permitted class/store byte basis; explicit null excludes that resource."""

    native_class_id: Digest
    store_binding_id: str
    component: StorageComponent | None


class StorageMapping(ProfileRecord):
    """The exact D152 operator mapping preimage, with no caller bytes or prices."""

    contract: Literal["rememberstack.storage_mapping.v1"] = (
        "rememberstack.storage_mapping.v1"
    )
    classification_digest: Digest
    store_binding_digest: Digest
    rules: tuple[MappingRule, ...]


class ManagedStorageCaptureProfile(ProfileRecord):
    """Current explicit managed configuration requiring independent acceptance."""

    contract: Literal["rememberstack.managed_storage_profile.v1"] = (
        "rememberstack.managed_storage_profile.v1"
    )
    native: NativeCaptureProfile
    managed_runtime_source_digest: Digest
    provider_profile_digest: Digest
    provider_clock_profile_digest: Digest
    bindings: ManagedStoreBindings
    classification: StorageClassification
    mapping: StorageMapping

    def request(self, *, cut_at_unix_us: str) -> StorageCaptureRequest:
        """Derive one identity from the exact accepted documents and requested cut."""
        if int(cut_at_unix_us) % 1_000_000:
            raise ValueError("managed_cut_not_whole_utc_second")
        return StorageCaptureRequest(
            deployment_id=self.bindings.deployment_id,
            cut_at_unix_us=cut_at_unix_us,
            store_binding_digest=profile_digest(record=self.bindings),
            schema_profile_digest=self.native.schema_profile_digest,
            classification_digest=profile_digest(record=self.classification),
            native_capture_profile_digest=self.native.profile_digest,
            operator_mapping_digest=profile_digest(record=self.mapping),
        )


def native_class_identity(*, name: str) -> str:
    """Bind an opaque class to its fixed source symbol, never source content."""
    return hashlib.sha256(
        b"rememberstack.native_class.v1\0" + name.encode("ascii")
    ).hexdigest()


def profile_bytes(*, record: BaseModel) -> bytes:
    """Encode the declared closed profile document under D152 canonical JSON."""
    return json.dumps(
        record.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def profile_digest(
    *, record: StorageClassification | StorageMapping | ManagedStoreBindings
) -> str:
    """Hash a complete typed profile with its declared contract/NUL framing."""
    return hashlib.sha256(
        record.contract.encode("ascii") + b"\0" + profile_bytes(record=record)
    ).hexdigest()


def current_class_rules() -> tuple[
    tuple[str, str, StoragePurpose, StorageComponent | None], ...
]:
    """Return the complete current source closure; unknown roots never inherit."""
    rows: list[tuple[str, str, StoragePurpose, StorageComponent | None]] = [
        (
            f"postgres.public.{root}",
            "postgres",
            "projection_origin" if root in _PG_PROJECTIONS else "truth_provenance",
            "postgres_allocated",
        )
        for root in _PG_INCLUDED
    ]
    for purpose, roots in _PG_EXCLUDED.items():
        rows.extend(
            (f"postgres.public.{root}", "postgres", purpose, None) for root in roots
        )
    if {row[0].removeprefix("postgres.public.") for row in rows} != set(_ROOTS) or len(
        rows
    ) != len(_ROOTS):
        raise ValueError("managed_source_root_closure_changed")
    rows.extend(_OBJECT_RULES)
    rows.extend(
        ("provider.directory", role, "adapter_metadata", None)
        for role in ("raw", "artifacts", "corpus", "forget")
    )
    return tuple(rows)


def build_managed_storage_profile(
    *,
    native: NativeCaptureProfile,
    bindings: ManagedStoreBindings,
    managed_runtime_source_digest: str,
    provider_profile_digest: str,
    provider_clock_profile_digest: str,
) -> ManagedStorageCaptureProfile:
    """Serialize the current explicit mapping for operator technical acceptance.

    This constructor never grants acceptance, qualification or enablement.
    Runtime must load its independently accepted exact profile and source pins.
    """
    bindings.filesystem_for(role="raw")
    rows = current_class_rules()
    purposes: dict[str, StoragePurpose] = {
        native_class_identity(name=name): purpose for name, _, purpose, _ in rows
    }
    classification = StorageClassification(
        source_digest=native.source_digest,
        schema_profile_digest=native.schema_profile_digest,
        rules=tuple(
            ClassificationRule(native_class_id=key, purpose=purpose)
            for key, purpose in sorted(purposes.items())
        ),
    )
    mapping = StorageMapping(
        classification_digest=profile_digest(record=classification),
        store_binding_digest=profile_digest(record=bindings),
        rules=tuple(
            sorted(
                (
                    MappingRule(
                        native_class_id=native_class_identity(name=name),
                        store_binding_id=store,
                        component=component,
                    )
                    for name, store, _, component in rows
                ),
                key=lambda row: (row.native_class_id, row.store_binding_id),
            )
        ),
    )
    return ManagedStorageCaptureProfile(
        native=native,
        bindings=bindings,
        classification=classification,
        mapping=mapping,
        managed_runtime_source_digest=managed_runtime_source_digest,
        provider_profile_digest=provider_profile_digest,
        provider_clock_profile_digest=provider_clock_profile_digest,
    )
