# Claude Opus review — D102 document-local exact T0

**Date:** 2026-08-31

**Reviewer:** Claude Code, `claude-opus-5`, effort `xhigh`

**Scope:** D102 analysis, decision, binding entity/registry/schema/LoCoMo
design amendments, and sequencing. Claude was instructed not to edit.

## Round 1 — `REQUEST_CHANGES`

Claude agreed with the document-local boundary and fail-closed intent, but
found that the canonical lemma was not queryable, conflicts counted only T4
anchors instead of every same-name document entity, T4 did not explicitly
stamp the new feature contract, generation compatibility was ambiguous, and
several current-protocol statements were stale.

The design was amended to stamp document/canonical-lemma coordinates on every
D102 decision, make T4 match the only authorizer, treat other same-name entity
membership as conflict, define compatibility by feature contract rather than
resolver generation, commit anchor hits in the first locked transaction, and
roll all current protocol wording to Full-v18.

## Round 2 — `REQUEST_CHANGES`

Claude verified those semantic fixes but found the proposed join over
partitioned mentions and resolution decisions unbounded in either drive
direction. Running it under the normalized-lemma advisory lock could create a
convoy for popular entities. It also found missing forward pointers, schema
documentation, and remaining stale current-protocol prose.

The design replaced the join with the bounded
`document_entity_bindings` projection, keyed by deployment/document/canonical
lemma/entity, and added a deployment generation gate, exact source-decision
partition coordinate, rebuild/repair posture, schema contract, and decision-log
forward pointers.

## Round 3 — `REQUEST_CHANGES`; findings applied

Claude confirmed that the new primary-key access path is bounded, source
validation is partition-pruned, conservative conflicts fail safe, the
rebuild/live-write race is avoided, and provider-path revalidation preserves
D99 concurrency. It then identified three lifecycle gaps and three contract
gaps:

1. D22's zero-false-merge same-name gate contradicted D102's explicitly
   accepted unseen-second-person risk.
2. Human re-decision/unmerge writers were not required to create conflict
   bindings.
3. Ordinary version/lineage deletion could retain an anchor grounded in
   deleted evidence.
4. The non-partitioned draft omitted real document/entity foreign keys.
5. The table lacked a scale/partitioning rationale.
6. Full-v18 did not verify that `document-t0-v1` was enabled.

## Disposition

All six round-3 findings were applied without expanding the identity policy:

- D22 now hard-gates cross-document homonyms and already-recorded local
  conflicts while reporting the accepted unseen-second-person case separately;
- review/re-decision and unmerge restoration use the same binding writer;
- ordinary version or lineage delete clears the whole document prefix;
- the table has real document/entity FKs and 64-way `HASH(doc_id)` partitioning;
- the schema records the cardinality/access rationale; and
- public readiness exposes the binding generation, which Full-v18 requires and
  fingerprints.

Optional review nits were kept minimal: replay records
`is_new_entity=false` and source-T4 confidence, and unused first/last-seen
columns were removed from the binding table.
