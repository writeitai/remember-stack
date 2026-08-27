"""Executable PostgreSQL catalog contract for the Phase 0 structural schema."""

from collections.abc import Iterable
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from sqlalchemy import Connection
from sqlalchemy import text

EXPECTED_EXTENSIONS: Final = (
    "btree_gist",
    "fuzzystrmatch",
    "pg_partman",
    "pg_textsearch",
    "pg_trgm",
    "pgcrypto",
    "unaccent",
    "vector",
)
# D41 claim valid-time vocabularies — must match the PostgreSQL enums and the
# Python StrEnums on CandidateClaim / ClaimRecord (drift-guarded in tests).
CLAIM_VALID_KIND_VALUES: Final = (
    "proposition_validity",
    "event_time",
    "measurement_period",
    "effective_period",
)
CLAIM_VALID_PRECISION_VALUES: Final = (
    "unknown",
    "instant",
    "day",
    "month",
    "quarter",
    "year",
    "open",
)

EXPECTED_ENUMS: Final = (
    "adjudication_method",
    "adjudication_outcome",
    "alias_provenance",
    "claim_temporal_class",
    "claim_valid_kind",
    "claim_valid_precision",
    "crossref_kind",
    "currency_reason",
    "decision_actor",
    "deployment_status",
    "document_origin",
    "document_status",
    "entity_status",
    "eval_suite",
    "evidence_stance",
    "extraction_decision_type",
    "forget_manifest_status",
    "golden_hardness",
    "golden_label",
    "grounding_audit_status",
    "knowledge_artifact_status",
    "knowledge_evidence_role",
    "knowledge_layer",
    "knowledge_page_kind",
    "knowledge_rule_kind",
    "knowledge_trigger",
    "ontology_status",
    "ontology_tier",
    "pipeline_component",
    "pipeline_stage",
    "plan_action",
    "plan_decision_status",
    "plan_trigger",
    "processing_defer_reason",
    "processing_lane",
    "processing_status",
    "processing_target",
    "projection_plane",
    "assured_answer_intent",
    "assured_operation_name",
    "assured_output_grain",
    "assured_result_contract",
    "refresh_status",
    "relation_status",
    "saved_query_assurance",
    "saved_query_origin",
    "saved_query_status",
    "resolution_tier",
    "review_item_kind",
    "review_status",
    "review_verdict",
    "rule_key_kind",
    "scope_interest_kind",
    "section_role",
    "selection_drop_reason",
    "selection_outcome",
    "skeleton_check_outcome",
    "snapshot_status",
    "structure_route_tag",
    "subscription_status",
    "surface_cost_kind",
    "surface_cost_outcome",
    "versioning_mode",
)
EXPECTED_TABLES: Final = (
    "aliases",
    "canary_cases",
    "chunk_claims",
    "chunk_search",
    "chunks",
    "claim_extraction_decisions",
    "claims",
    "connector_sync_cycles",
    "content_objects",
    "cost_ledger",
    "deployment_extension_packs",
    "deployments",
    "document_crossrefs",
    "document_representations",
    "document_skeleton_checks",
    "document_sections",
    "document_structure_generations",
    "document_versions",
    "documents",
    "entities",
    "entity_types",
    "eval_runs",
    "extension_packs",
    "forget_manifests",
    "generic_identifier_guard",
    "golden_claim_labels",
    "golden_pairs",
    "grounding_audits",
    "knowledge_artifact_evidence",
    "knowledge_artifacts",
    "knowledge_compilations",
    "knowledge_dispatches",
    "knowledge_page_rules",
    "knowledge_page_watches",
    "knowledge_plan_decisions",
    "knowledge_plan_runs",
    "knowledge_quarantines",
    "knowledge_refresh_queue",
    "knowledge_rule_keys",
    "knowledge_subscriptions",
    "mentions",
    "normalize_observation_staging",
    "merge_events",
    "obs_flush_entity_units",
    "obs_flush_version_state",
    "p1_search_channels",
    "observation_adjudications",
    "observation_evidence",
    "observations",
    "pipeline_component_versions",
    "predicates",
    "processing_state",
    "projection_snapshots",
    "relation_adjudications",
    "relation_evidence",
    "relations",
    "resolution_decisions",
    "resolution_exclusions",
    "resolver_versions",
    "assured_operations",
    "review_queue",
    "saved_queries",
    "saved_query_audit",
    "saved_query_registry_state",
    "saved_query_versions",
    "scope_interests",
    "scopes",
    "surface_cost_ledger",
    "surface_cost_meter_state",
    "testimony_currency_events",
)
EXPECTED_INDEXES: Final = (
    "ix_adjud_live",
    "ix_adjud_relation",
    "ix_aliases_entity",
    "ix_aliases_lemma_dm",
    "ix_aliases_lemma_exact",
    "ix_aliases_lemma_trgm",
    "ix_chunkclaims_claim",
    "ix_chunk_search_bm25",
    "ix_chunk_search_embedding_hnsw",
    "ix_chunks_doc",
    "ix_chunks_reuse",
    "ix_chunks_section",
    "ix_chunks_version",
    "ix_claims_audit",
    "ix_claims_chunk",
    "ix_claims_current",
    "ix_claims_current_bm25",
    "ix_claims_current_embedding_hnsw",
    "ix_claims_doc",
    "ix_claims_flagged",
    "ix_claims_valid_window",
    "ix_cost_budget_window",
    "ix_cost_export",
    "ix_crossrefs_from",
    "ix_crossrefs_to",
    "ix_currency_claim",
    "ix_currency_doc",
    "ix_cxd_chunk",
    "ix_cxd_drops",
    "ix_docreps_version",
    "ix_documents_entity",
    "ix_documents_live",
    "ix_docversions_doc",
    "ix_docversions_hash",
    "ix_docversions_status",
    "ix_entities_name_trgm",
    "ix_entities_embedding_hnsw",
    "ix_entities_redirect",
    "ix_entity_types_parent",
    "ix_eval_suite_ver",
    "ix_forget_content_guard",
    "ix_forget_source_guard",
    "ix_grounding_claim",
    "ix_kae_claim_coordinate",
    "ix_kae_doc",
    "ix_kae_relation",
    "ix_kartifacts_parent",
    "ix_kartifacts_scope",
    "ix_kartifacts_stale",
    "ix_kcompilations_artifact",
    "ix_kdispatch_pending",
    "ux_kdispatch_pending_subscription",
    "ix_kplan_proposed",
    "ix_kplan_runs_deployment",
    "ix_krefresh_runnable",
    "ux_krefresh_open_authored_review",
    "ix_krule_keys_lookup",
    "ix_kwatch_watched",
    "ix_mentions_claim",
    "ix_mentions_doc",
    "ix_merge_absorbed",
    "ix_merge_survivor",
    "ix_merge_trigger",
    "ix_obsadjud_live",
    "ix_obsadjud_observation",
    "ix_observations_block",
    "ix_observations_contradiction",
    "ix_observations_entity",
    "ix_observations_embedding_hnsw",
    "ix_obsevidence_claim",
    "ix_predicates_other",
    "ix_procstate_dlq",
    "ix_procstate_due",
    "ix_procstate_target",
    "ix_relations_block_obj",
    "ix_relations_block_subj",
    "ix_relations_contradiction",
    "ix_relations_live",
    "ix_relations_embedding_hnsw",
    "ix_relations_predicate",
    "ix_relevidence_claim",
    "ix_resdec_entity",
    "ix_resdec_entity_live",
    "ix_resdec_live",
    "ix_resdec_mention",
    "ix_review_pending",
    "ix_sections_doc",
    "ix_sections_parent",
    "ix_sections_role",
    "ix_skeleton_checks_representation",
    "ix_structure_generations_representation",
    "ix_surface_cost_export",
    "ux_kae_link",
    "ux_kquarantine_open_artifact",
    "ux_kwatch",
    "ux_snapshot_latest",
)
EXPECTED_RANGE_PARENTS: Final = {
    "chunk_claims": "created_at",
    "chunks": "created_at",
    "claim_extraction_decisions": "decided_at",
    "mentions": "created_at",
    "resolution_decisions": "decided_at",
    "surface_cost_ledger": "occurred_at",
    "testimony_currency_events": "occurred_at",
}
EXPECTED_HASH_PARENTS: Final = ("observation_evidence", "relation_evidence")
UNLANED_STAGES: Final = frozenset(
    {
        "refresh_profile",
        "compile_knowledge",
        "reflect_knowledge",
        "lint_knowledge",
        "dispatch_knowledge",
        "hard_forget",
    }
)
"""Scheduled aggregate and deployment-orchestration stages whose route is unlaned.

Laned-ness is enforced here, at the spine enqueue path — not by a stage-enumerating
database CHECK, which would need a migration edit for every new stage (D67, simplified
2026-07-18).
"""


def lane_is_valid(*, stage: str, lane: str | None) -> bool:
    """Return whether a lane value is legal for a stage's route (D67).

    Plane-E stages require a concrete lane (steady or backfill); scheduled K/P
    and hard-forget orchestration stages must be unlaned (SQL NULL).
    """
    return (lane is None) == (stage in UNLANED_STAGES)


EXPECTED_VIEWS: Final = (
    "v_cost_receipts",
    "v_graph_survivor",
    "v_memory_entity_survivor",
    "v_memory_evidence_lineage_live",
    "v_memory_fact_claim_live",
    "v_memory_fact_visible",
    "v_memory_mention_current_content",
    "v_memory_page_citation_visible",
)
QUERY_SPACE_SCHEMA: Final = "memory_v1"
"""The versioned public query space. Its relation set is owned by the checked-in
`memory_v1` manifest (`rememberstack.spine.query_space`); this module only
proves the schema exists at head and is gone after a downgrade, so a stray
leftover schema cannot masquerade as the query surface."""
EMPTY_AT_HEAD: Final = ("deployments", "entity_types", "predicates")
# Includes saved_query_registry_state and saved_query_audit (Batch E governance).
# Counts are measured against a fresh head inventory; update when the registry
# migration gains or loses constraints.
# PostgreSQL 19 represents NOT NULL declarations as first-class `n` rows in
# pg_constraint. The catalog contract pins them with the other structural
# constraint kinds instead of pretending the database still exposes PG16's shape.
EXPECTED_CONSTRAINT_COUNTS: Final = {
    "c": 68,
    "f": 121,
    "n": 537,
    "p": 70,
    "u": 35,
    "x": 1,
}
DECISION_OBJECTS: Final = {
    "D1": ("pipeline_component_versions",),
    "D2": ("claims", "relations", "relation_evidence"),
    "D17": ("aliases", "ix_aliases_lemma_dm", "ix_aliases_lemma_trgm"),
    "D23": ("claims", "relation_evidence", "observation_evidence"),
    "D31": ("claims", "grounding_audits"),
    "D36": ("documents", "document_versions", "document_crossrefs"),
    "D45": ("knowledge_artifacts", "knowledge_plan_runs", "knowledge_refresh_queue"),
    "D46": ("knowledge_artifacts", "knowledge_quarantines"),
    "D50": ("assured_operations",),
    "D87": ("assured_operations",),
    "D54": ("testimony_currency_events", "ix_claims_current"),
    "D55": ("document_versions", "document_representations"),
    "D56": ("chunks", "chunk_claims"),
    "D57": ("document_sections", "document_representations"),
    "D65": ("documents", "document_versions", "document_representations"),
    "D67": ("processing_state", "cost_ledger", "ix_procstate_due"),
    "D68": ("deployments", "ix_entities_name_trgm"),
    "D69": ("relations", "v_memory_entity_survivor"),
    "D74": ("forget_manifests", "ix_forget_content_guard"),
    "D79": ("document_structure_generations", "document_skeleton_checks"),
    "D91": (
        "surface_cost_ledger",
        "surface_cost_meter_state",
        "v_cost_receipts",
        "ix_surface_cost_export",
        "ix_cost_export",
    ),
    "D94": (
        "chunk_search",
        "p1_search_channels",
        "ix_chunk_search_embedding_hnsw",
        "ix_chunk_search_bm25",
        "ix_claims_current_embedding_hnsw",
        "ix_claims_current_bm25",
    ),
}


class SchemaContractError(RuntimeError):
    """Raised with all observed catalog differences from the binding design."""


class CatalogInventory(BaseModel):
    """Stable machine-readable evidence returned by a successful verification."""

    model_config = ConfigDict(frozen=True)

    server_version: str
    extensions: tuple[str, ...]
    enums: tuple[str, ...]
    tables: tuple[str, ...]
    indexes: tuple[str, ...]
    range_parents: tuple[str, ...]
    hash_parents: tuple[str, ...]
    hash_child_counts: dict[str, int]
    views: tuple[str, ...]
    query_space_views: tuple[str, ...]
    constraint_counts: dict[str, int]
    commented_tables: int
    commented_columns: int
    empty_tables: tuple[str, ...]
    decisions_covered: tuple[str, ...]


def verify_schema(connection: Connection) -> CatalogInventory:
    """Verify the complete structural contract and return observed evidence."""
    problems: list[str] = []
    extensions = _string_values(
        connection=connection,
        query="SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY 1",
    )
    enums = _string_values(
        connection=connection,
        query=(
            "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
            "WHERE n.nspname='public' AND t.typtype='e' ORDER BY 1"
        ),
    )
    tables = _named_relations(
        connection=connection, names=EXPECTED_TABLES, kinds=("r", "p")
    )
    indexes = _string_values(
        connection=connection,
        query=(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
            "AND indexname = ANY(:names) ORDER BY 1"
        ),
        names=EXPECTED_INDEXES,
    )
    views = _named_relations(connection=connection, names=EXPECTED_VIEWS, kinds=("v",))
    query_space_views = _string_values(
        connection=connection,
        query=(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=:schema AND c.relkind='v' ORDER BY 1"
        ),
        schema=QUERY_SPACE_SCHEMA,
    )
    if not query_space_views:
        problems.append(f"{QUERY_SPACE_SCHEMA} query space is missing at head")
    _compare(
        label="extensions",
        actual=extensions,
        expected=EXPECTED_EXTENSIONS,
        problems=problems,
    )
    _compare(
        label="enum types", actual=enums, expected=EXPECTED_ENUMS, problems=problems
    )
    _compare(
        label="UGM tables", actual=tables, expected=EXPECTED_TABLES, problems=problems
    )
    chunk_search_orphans = int(
        connection.execute(
            statement=text(
                "SELECT count(*) FROM chunk_search AS search "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM chunks AS authority "
                "WHERE authority.deployment_id = search.deployment_id "
                "AND authority.chunk_id = search.chunk_id)"
            )
        ).scalar_one()
    )
    if chunk_search_orphans:
        problems.append(
            f"chunk_search contains {chunk_search_orphans} rows without chunk authority"
        )
    _compare(
        label="explicit indexes",
        actual=indexes,
        expected=EXPECTED_INDEXES,
        problems=problems,
    )
    _compare(
        label="projection views",
        actual=views,
        expected=EXPECTED_VIEWS,
        problems=problems,
    )

    partition_rows = connection.execute(
        statement=text(
            "SELECT c.relname, p.partstrat FROM pg_partitioned_table p "
            "JOIN pg_class c ON c.oid=p.partrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname = ANY(:names) ORDER BY 1"
        ),
        parameters={"names": sorted((*EXPECTED_RANGE_PARENTS, *EXPECTED_HASH_PARENTS))},
    ).all()
    partition_strategies = {str(row[0]): str(row[1]) for row in partition_rows}
    expected_strategies = {
        **dict.fromkeys(EXPECTED_RANGE_PARENTS, "r"),
        **dict.fromkeys(EXPECTED_HASH_PARENTS, "h"),
    }
    if partition_strategies != expected_strategies:
        problems.append(
            f"partition strategies: expected {expected_strategies}, observed {partition_strategies}"
        )

    partman_rows = connection.execute(
        statement=text(
            "SELECT parent_table, control, partition_interval FROM public.part_config "
            "WHERE parent_table = ANY(:names) ORDER BY 1"
        ),
        parameters={"names": [f"public.{name}" for name in EXPECTED_RANGE_PARENTS]},
    ).all()
    observed_partman = {
        str(row[0]).removeprefix("public."): (str(row[1]), str(row[2]))
        for row in partman_rows
    }
    expected_partman = {
        parent: (control, "1 mon") for parent, control in EXPECTED_RANGE_PARENTS.items()
    }
    if observed_partman != expected_partman:
        problems.append(
            f"pg_partman parents: expected {expected_partman}, observed {observed_partman}"
        )

    hash_child_counts: dict[str, int] = {}
    for parent in EXPECTED_HASH_PARENTS:
        child_rows = connection.execute(
            statement=text(
                "SELECT child.relname, pg_get_expr(child.relpartbound, child.oid) "
                "FROM pg_inherits i JOIN pg_class parent ON parent.oid=i.inhparent "
                "JOIN pg_class child ON child.oid=i.inhrelid "
                "JOIN pg_namespace n ON n.oid=parent.relnamespace "
                "WHERE n.nspname='public' AND parent.relname=:parent ORDER BY child.relname"
            ),
            parameters={"parent": parent},
        ).all()
        observed_names = {str(row[0]) for row in child_rows}
        expected_names = {f"{parent}_p{remainder}" for remainder in range(64)}
        hash_child_counts[parent] = len(child_rows)
        if observed_names != expected_names:
            problems.append(
                f"{parent} hash children: missing {sorted(expected_names - observed_names)}, "
                f"unexpected {sorted(observed_names - expected_names)}"
            )
        for name, bound in child_rows:
            remainder = int(str(name).rsplit("p", maxsplit=1)[1])
            normalized_bound = " ".join(str(bound).lower().split())
            if f"modulus 64, remainder {remainder}" not in normalized_bound:
                problems.append(f"{name} has incorrect hash bound {bound}")

    constraint_rows = connection.execute(
        statement=text(
            "SELECT con.contype, count(*) FROM pg_constraint con "
            "JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname = ANY(:tables) "
            "GROUP BY con.contype ORDER BY con.contype"
        ),
        parameters={"tables": list(EXPECTED_TABLES)},
    ).all()
    constraint_counts = {str(row[0]): int(row[1]) for row in constraint_rows}
    if constraint_counts != EXPECTED_CONSTRAINT_COUNTS:
        problems.append(
            f"constraint counts: expected {EXPECTED_CONSTRAINT_COUNTS}, observed {constraint_counts}"
        )
    primary_keys = _string_values(
        connection=connection,
        query=(
            "SELECT c.relname FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND con.contype='p' AND c.relname = ANY(:names) ORDER BY 1"
        ),
        names=EXPECTED_TABLES,
    )
    _compare(
        label="table primary keys",
        actual=primary_keys,
        expected=EXPECTED_TABLES,
        problems=problems,
    )

    constraint_definitions = "\n".join(
        str(row[0]).lower()
        for row in connection.execute(
            statement=text(
                "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
                "JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname = ANY(:tables)"
            ),
            parameters={"tables": list(EXPECTED_TABLES)},
        )
    )
    required_constraint_fragments = (
        "exclude using gist",
        "num_nonnulls(claim_lineage_id, relation_id, doc_id) = 1",
        "(claim_lineage_id is null) = (claim_chunk_content_hash is null)",
        "num_nonnulls(artifact_id, subscription_id) = 1",
        "anchor_ok and window_membership_ok",
        "unique (deployment_id, processing_id, attempt, call_key)",
        "unique (deployment_id, target_kind, target_id, stage, component_version)",
    )
    for fragment in required_constraint_fragments:
        if fragment not in constraint_definitions:
            problems.append(f"required constraint definition missing: {fragment}")

    commented_tables = int(
        connection.execute(
            statement=text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname = ANY(:tables) "
                "AND obj_description(c.oid, 'pg_class') IS NOT NULL"
            ),
            parameters={"tables": list(EXPECTED_TABLES)},
        ).scalar_one()
    )
    if commented_tables != len(EXPECTED_TABLES):
        problems.append(
            f"table comments: expected {len(EXPECTED_TABLES)}, observed {commented_tables}"
        )
    commented_columns = int(
        connection.execute(
            statement=text(
                "SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
                "AND c.relname = ANY(:tables) AND a.attnum > 0 AND NOT a.attisdropped "
                "AND col_description(c.oid, a.attnum) IS NOT NULL"
            ),
            parameters={"tables": list(EXPECTED_TABLES)},
        ).scalar_one()
    )
    if commented_columns < 300:
        problems.append(
            f"column comments: expected at least 300, observed {commented_columns}"
        )

    function_count = int(
        connection.execute(
            statement=text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname='notify_due_processing_insert'"
            )
        ).scalar_one()
    )
    trigger_count = int(
        connection.execute(
            statement=text(
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                "WHERE c.relname='processing_state' AND t.tgname='tr_processing_state_initial_wake' "
                "AND NOT t.tgisinternal"
            )
        ).scalar_one()
    )
    if function_count != 1 or trigger_count != 1:
        problems.append(
            f"wake function/trigger: expected 1/1, observed {function_count}/{trigger_count}"
        )

    relates_definition = str(
        connection.execute(
            statement=text(
                "SELECT pg_get_viewdef("
                "'rememberstack_graph_internal.relations_history'::regclass, true)"
            )
        ).scalar_one()
    ).lower()
    if (
        "r.invalidated_at is null" in relates_definition
        or "invalidated_at >" in relates_definition
    ):
        problems.append(
            "relations_history contains a forbidden current/invalidation filter"
        )
    for required_view_fragment in (
        "r.invalidated_at",
        "v_memory_entity_survivor subject",
        "v_memory_entity_survivor object",
        "relations r",
        "relation_evidence",
    ):
        if required_view_fragment not in relates_definition:
            problems.append(f"relations_history missing {required_view_fragment}")

    empty_tables: list[str] = []
    for table_name in EMPTY_AT_HEAD:
        count = int(
            connection.execute(
                statement=text(f"SELECT count(*) FROM {table_name}")
            ).scalar_one()
        )
        if count != 0:
            problems.append(
                f"{table_name} must be empty at migration head, observed {count}"
            )
        else:
            empty_tables.append(table_name)

    available_objects = {*tables, *indexes, *views}
    for decision, objects in DECISION_OBJECTS.items():
        missing = set(objects) - available_objects
        if missing:
            problems.append(
                f"{decision} mapping missing catalog objects {sorted(missing)}"
            )

    if problems:
        raise SchemaContractError(
            "schema contract mismatch:\n- " + "\n- ".join(problems)
        )

    server_version = str(
        connection.execute(
            statement=text("SELECT version()"), parameters={}
        ).scalar_one()
    )
    return CatalogInventory(
        server_version=server_version,
        extensions=extensions,
        enums=enums,
        tables=tables,
        indexes=indexes,
        range_parents=tuple(EXPECTED_RANGE_PARENTS),
        hash_parents=EXPECTED_HASH_PARENTS,
        hash_child_counts=hash_child_counts,
        views=views,
        query_space_views=query_space_views,
        constraint_counts=constraint_counts,
        commented_tables=commented_tables,
        commented_columns=commented_columns,
        empty_tables=tuple(empty_tables),
        decisions_covered=tuple(DECISION_OBJECTS),
    )


def verify_schema_absent(connection: Connection) -> None:
    """Verify downgrade-to-base removed only the UGM-owned structural objects."""
    problems: list[str] = []
    relations = _named_relations(
        connection=connection,
        names=(*EXPECTED_TABLES, *EXPECTED_VIEWS),
        kinds=("r", "p", "v"),
    )
    enums = _string_values(
        connection=connection,
        query=(
            "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
            "WHERE n.nspname='public' AND t.typtype='e' AND t.typname = ANY(:names) ORDER BY 1"
        ),
        names=EXPECTED_ENUMS,
    )
    partman_config_exists = bool(
        connection.execute(
            statement=text("SELECT to_regclass('public.part_config') IS NOT NULL")
        ).scalar_one()
    )
    partman_count = 0
    if partman_config_exists:
        partman_count = int(
            connection.execute(
                statement=text(
                    "SELECT count(*) FROM public.part_config "
                    "WHERE parent_table = ANY(:parents)"
                ),
                parameters={
                    "parents": [f"public.{parent}" for parent in EXPECTED_RANGE_PARENTS]
                },
            ).scalar_one()
        )
    template_count = int(
        connection.execute(
            statement=text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname = ANY(:templates)"
            ),
            parameters={
                "templates": [
                    f"template_public_{parent}" for parent in EXPECTED_RANGE_PARENTS
                ]
            },
        ).scalar_one()
    )
    function_count = int(
        connection.execute(
            statement=text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname='notify_due_processing_insert'"
            )
        ).scalar_one()
    )
    query_space_present = bool(
        connection.execute(
            statement=text("SELECT count(*) FROM pg_namespace WHERE nspname = :schema"),
            parameters={"schema": QUERY_SPACE_SCHEMA},
        ).scalar_one()
    )
    if relations:
        problems.append(f"UGM relations remain: {relations}")
    if query_space_present:
        problems.append(f"UGM {QUERY_SPACE_SCHEMA} query space remains")
    if enums:
        problems.append(f"UGM enum types remain: {enums}")
    if partman_count:
        problems.append(f"UGM pg_partman registrations remain: {partman_count}")
    if template_count:
        problems.append(f"UGM pg_partman templates remain: {template_count}")
    if function_count:
        problems.append(f"UGM wake function remains: {function_count}")
    if problems:
        raise SchemaContractError(
            "downgrade cleanup mismatch:\n- " + "\n- ".join(problems)
        )


def _named_relations(
    *, connection: Connection, names: Iterable[str], kinds: tuple[str, ...]
) -> tuple[str, ...]:
    """Return matching public relation names with the requested relkinds."""
    return _string_values(
        connection=connection,
        query=(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname = ANY(:names) "
            "AND c.relkind = ANY(:kinds) ORDER BY 1"
        ),
        names=tuple(names),
        kinds=kinds,
    )


def _string_values(
    *,
    connection: Connection,
    query: str,
    names: Iterable[str] | None = None,
    kinds: Iterable[str] | None = None,
    schema: str | None = None,
) -> tuple[str, ...]:
    """Execute a one-column catalog query and return stable strings."""
    parameters: dict[str, object] = {}
    if names is not None:
        parameters["names"] = list(names)
    if kinds is not None:
        parameters["kinds"] = list(kinds)
    if schema is not None:
        parameters["schema"] = schema
    return tuple(
        str(value)
        for value in connection.execute(
            statement=text(query), parameters=parameters
        ).scalars()
    )


def _compare(
    *, label: str, actual: Iterable[str], expected: Iterable[str], problems: list[str]
) -> None:
    """Append an exact set difference to the accumulated contract problems."""
    actual_set = set(actual)
    expected_set = set(expected)
    if actual_set != expected_set:
        problems.append(
            f"{label}: missing {sorted(expected_set - actual_set)}, "
            f"unexpected {sorted(actual_set - expected_set)}"
        )
