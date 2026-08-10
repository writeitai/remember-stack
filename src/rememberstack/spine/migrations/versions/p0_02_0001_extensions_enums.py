"""Create required extensions and authoritative enum types."""

from collections.abc import Sequence

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables
from rememberstack.spine.migrations._helpers import drop_types

revision: str = "p0_02_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- gen_random_uuid fallback; digests
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- T1 fuzzy blocking: trigram GIN on names (D17)
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch; -- T2 phonetic: daitch_mokotoff() (D17, NOT soundex)
CREATE EXTENSION IF NOT EXISTS unaccent;      -- accent-fold names before trigram/phonetic (registries §5)
CREATE EXTENSION IF NOT EXISTS btree_gist;    -- composite GiST: relations bi-temporal EXCLUDE constraint (§9)
CREATE EXTENSION IF NOT EXISTS pg_partman;    -- monthly RANGE partition automation (D23, §12)
CREATE TYPE deployment_status      AS ENUM ('active','suspended','archived');

-- Every non-deterministic producer that stamps a *_version resolving to pipeline_component_versions
-- has a value here (so a version string can always resolve to a catalog row, D1/D12):
CREATE TYPE pipeline_component     AS ENUM (
  'ingester','converter','blockizer','structurer','crossreferencer','chunker','context_prefixer',
  'extractor','grounder','resolver','normalizer','adjudicator','embedder','fact_labeler',
  'profile_summarizer','community_detector','snapshot_builder','knowledge_planner',
  'knowledge_writer','knowledge_reflector','knowledge_linter','judge');
CREATE TYPE processing_target      AS ENUM ('document','document_section','chunk','claim','relation','observation','entity','snapshot','knowledge_artifact');
CREATE TYPE pipeline_stage         AS ENUM ('ingest','convert','structure','crossref','chunk','embed_chunk','extract_claims','embed_claim','ground_claims','resolve_entities','normalize_relations','adjudicate_supersession','adjudicate_observations','embed_relation','label_relation','embed_observation','label_observation','refresh_profile','build_snapshot','detect_communities','compile_knowledge','reflect_knowledge','lint_knowledge');
CREATE TYPE processing_status      AS ENUM ('pending','running','succeeded','failed','dead_letter','skipped');
-- D67: only plane-E routes use operational lanes. K/P (and other scheduled aggregate) jobs
-- represent their single unlaned route with SQL NULL, never a synthetic third enum value.
CREATE TYPE processing_lane        AS ENUM ('steady','backfill');
CREATE TYPE processing_defer_reason AS ENUM ('scheduled','retry_backoff','budget');

CREATE TYPE ontology_tier          AS ENUM ('core','extension','other','deprecated');
CREATE TYPE ontology_status        AS ENUM ('active','deprecated');
CREATE TYPE scope_interest_kind    AS ENUM ('entity_type','predicate','metadata','keyword');

CREATE TYPE entity_status          AS ENUM ('active','merged','retired');
CREATE TYPE alias_provenance       AS ENUM ('source','llm_canonical');
CREATE TYPE resolution_tier        AS ENUM ('T0','T1','T2','T3','T4_small','T4_frontier','human');
CREATE TYPE decision_actor         AS ENUM ('auto','human');

CREATE TYPE review_item_kind       AS ENUM ('merge_cluster','split_cluster','type_conflict','generic_identifier','contradiction','support_withdrawn');
CREATE TYPE review_status          AS ENUM ('pending','accepted','rejected','deferred','auto_resolved');
-- Covers all review_item_kinds (D24), not just merges: pick_a/pick_b/both_stand for contradictions,
-- downweight/keep_signal for generic_identifier, retype for type_conflict.
CREATE TYPE review_verdict         AS ENUM ('merge','not_merge','split','retype','downweight','keep_signal','pick_a','pick_b','both_stand','uncertain','restore_support','invalidate_fact');
-- restore_support / invalidate_fact are the two terminal verdicts of the support_withdrawn kind
-- (D54): restore = old claim regains currency ('review_restored' event) + the case is planted as
-- a D35 canary; invalidate = the fact's invalidated_at is set with a recorded adjudication.
-- 'uncertain' leaves the fact standing with its support:withdrawn marker (visibly unresolved).
CREATE TYPE golden_label           AS ENUM ('match','no_match');
CREATE TYPE golden_hardness        AS ENUM ('hard_positive','hard_negative','easy');
CREATE TYPE eval_suite             AS ENUM ('resolution','selection','grounding','retrieval','contradiction');
CREATE TYPE selection_outcome      AS ENUM ('keep','rewrite','drop','kept_flagged');

CREATE TYPE document_status        AS ENUM ('ingesting','converting','structuring','ready','failed','deleted');
CREATE TYPE document_origin        AS ENUM ('external','system_generated');  -- D42, stamped at E0 ingest per lineage
-- D55 lineage semantics: snapshot = every version is independent dated testimony forever;
-- living = the current version is the source's standing statement (currency follows it, D54):
CREATE TYPE versioning_mode        AS ENUM ('snapshot','living');
-- D54 testimony-currency transitions (append-only ledger; bookkeeping, never validity):
CREATE TYPE currency_reason        AS ENUM ('reextracted','version_superseded','version_deleted','review_restored');
-- 'review_restored' = the support_withdrawn triage's verdict A (became_current=true): a reviewer
-- judged the old claim correct and the new extractor regressed — the old claim stands as the
-- chunk's current transcription until a fixed extractor re-derives it (lifecycle §4).
CREATE TYPE section_role           AS ENUM ('body','abstract','introduction','results','methods','discussion','conclusion','references','appendix','table','figure_caption','nav','boilerplate','legal');
CREATE TYPE crossref_kind          AS ENUM ('cites','links_to','attaches','replies_to');

CREATE TYPE claim_temporal_class   AS ENUM ('static','dynamic','atemporal');
-- D41 source-asserted validity on claims (immutable; never a relation-style revisable window):
CREATE TYPE claim_valid_precision  AS ENUM ('unknown','instant','day','month','quarter','year','open');
CREATE TYPE claim_valid_kind       AS ENUM ('proposition_validity','event_time','measurement_period','effective_period');
CREATE TYPE grounding_audit_status AS ENUM ('unaudited','sampled_pass','sampled_fail','escalated');
-- The ledger records DROPs, low-confidence keeps (flags), and decontextualization EDITs.
-- Plain keeps are NOT persisted (they ARE the claims row); see §8.
CREATE TYPE extraction_decision_type AS ENUM ('selection_drop','selection_keep_flagged','decontext_edit');
CREATE TYPE selection_drop_reason  AS ENUM ('opinion','advice','hypothetical','generic','question','intro','conclusion','no_info','ambiguous','references_boilerplate');

CREATE TYPE evidence_stance        AS ENUM ('supports','contradicts');
CREATE TYPE relation_status        AS ENUM ('active','invalidated');  -- generated mirror of invalidated_at; retirement (zero-evidence GC, §13) = setting invalidated_at
CREATE TYPE adjudication_outcome   AS ENUM ('add','noop','supersede','contradict','same_as_merge_proposal','retracted_source_removal');
-- 'retracted_source_removal' = D54/D55 source-acted closure: a living lineage's removal, OR
-- any deletion (version / lineage / source-observed), withdrew a fact's sole current support →
-- adjudicated closed per shape (states: valid_until cap; measurements: invalidated_at — the
-- D43 no-cap rule); the adjudication row is the audit record. (support_withdrawn is
-- exclusively the RE-EXTRACTION zero-support flag, D54 — never removal, never deletion.)
CREATE TYPE adjudication_method    AS ENUM ('novelty_gate','exact','fuzzy','embedding','small_model','frontier_llm');

CREATE TYPE projection_plane       AS ENUM ('P1_search','P2_graph','P3_corpusfs');
CREATE TYPE snapshot_status        AS ENUM ('building','validating','published','superseded','failed');
CREATE TYPE community_algorithm    AS ENUM ('leiden','louvain');  -- external detection pass (D11)

CREATE TYPE knowledge_layer        AS ENUM ('K1','K2','K3');  -- content TIERS of one mechanism (D47), not separate machinery
CREATE TYPE knowledge_page_kind    AS ENUM ('compiled','authored');  -- D46: machine-owned body vs human/agent-owned body
-- 'quarantined' = a compiled body was human-edited directly; excluded from recompile until the
-- diff is triaged — into the curation sidecar, or by adopting the page as authored
-- (plan_action 'convert_kind'; D46 quarantine rule):
CREATE TYPE knowledge_artifact_status AS ENUM ('active','stale','quarantined','tombstoned');
CREATE TYPE knowledge_evidence_role AS ENUM ('supports','contradicts','cites');
-- D45 routing rules — the closed, mechanically-evaluable kind set (k_layers_design.md §5):
CREATE TYPE knowledge_rule_kind    AS ENUM ('entity','entity_subtree','predicate_beat','community','doc_set','scope_interests','manual');
CREATE TYPE rule_key_kind          AS ENUM ('entity','predicate','community','doc_source');
-- convert_kind = D46 adoption (compiled→authored, via quarantine triage) / handover
-- (authored→compiled — the one action that NEVER auto-applies; requires author confirmation):
CREATE TYPE plan_action            AS ENUM ('create_page','split_page','merge_pages','move_page','retire_page','adjust_rule','convert_kind');
CREATE TYPE plan_trigger           AS ENUM ('orphan_evidence','size_overflow','community_change','reflection','writer_suggestion','human');
CREATE TYPE plan_decision_status   AS ENUM ('proposed','applied','rejected');
CREATE TYPE subscription_status    AS ENUM ('active','paused','retired');  -- dispatch consumers (k_layers §5)
-- 'authored_review' = cited/watched evidence changed under an AUTHORED page → review flag for
-- the page's author (human or agent), never an auto-recompile (D46):
CREATE TYPE knowledge_trigger      AS ENUM ('evidence_changed','community_changed','debounce_timer','manual','tombstone','authored_review');
CREATE TYPE refresh_status         AS ENUM ('pending','running','done','failed');

-- D50/D87 closed assured-operation registry (§11.A):
CREATE TYPE assured_operation_name AS ENUM ('resolve_entity','testimony_context','fact_context','answer_context');
CREATE TYPE assured_result_contract AS ENUM ('envelope','context_bundle_v1');
CREATE TYPE assured_output_grain AS ENUM ('fact','evidence','compiled','composite');
CREATE TYPE assured_answer_intent AS ENUM ('identity','testimony','facts','combined_context');
"""
_TABLES = ()
_TYPES = (
    "deployment_status",
    "pipeline_component",
    "processing_target",
    "pipeline_stage",
    "processing_status",
    "processing_lane",
    "processing_defer_reason",
    "ontology_tier",
    "ontology_status",
    "scope_interest_kind",
    "entity_status",
    "alias_provenance",
    "resolution_tier",
    "decision_actor",
    "review_item_kind",
    "review_status",
    "review_verdict",
    "golden_label",
    "golden_hardness",
    "eval_suite",
    "selection_outcome",
    "document_status",
    "document_origin",
    "versioning_mode",
    "currency_reason",
    "section_role",
    "crossref_kind",
    "claim_temporal_class",
    "claim_valid_precision",
    "claim_valid_kind",
    "grounding_audit_status",
    "extraction_decision_type",
    "selection_drop_reason",
    "evidence_stance",
    "relation_status",
    "adjudication_outcome",
    "adjudication_method",
    "projection_plane",
    "snapshot_status",
    "community_algorithm",
    "knowledge_layer",
    "knowledge_page_kind",
    "knowledge_artifact_status",
    "knowledge_evidence_role",
    "knowledge_rule_kind",
    "rule_key_kind",
    "plan_action",
    "plan_trigger",
    "plan_decision_status",
    "subscription_status",
    "knowledge_trigger",
    "refresh_status",
    "assured_operation_name",
    "assured_result_contract",
    "assured_output_grain",
    "assured_answer_intent",
)


def upgrade() -> None:
    """Apply create required extensions and authoritative enum types."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Revert create required extensions and authoritative enum types."""
    drop_tables(table_names=reversed(_TABLES))
    drop_types(type_names=reversed(_TYPES))
