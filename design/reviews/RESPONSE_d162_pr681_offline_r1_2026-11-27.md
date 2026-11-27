# Response to PR #681 offline grid review, round 1

Reviewed head `11b3b805f`. The review reported no P0 findings.

## P1

- **Typed location in extraction reuse.** A local probe reproduced the
  `Orchard` to `Terrace` rename. The consumed bytes, relative origin hashes,
  catalogue, E1 keys and D80 embedding hashes matched. E2's grounding
  accepted `Orchard` in the old version and rejected it in the new one.
  When either grid is split, Selection and Claimify reuse now check E2's
  ordered location pairs for the target and each preceding D122 producer
  used by Claimify, including empty results. D140 date inheritance still
  uses its date-free evidence. The implementation checks cover the target
  rename, a producer-only change and a matching control with reused claim
  IDs and zero paid calls.
  [D80 render](../../src/rememberstack/core/embedding_input_policy.py#L188-L234) ·
  [E2 bundle and grounding](../../src/rememberstack/workers/e2.py#L1596-L1737) ·
  [D162 reuse](../../plan/designs/split_oversized_chunks_design.md#2-coordinates-and-reuse)

## P2

- **Fitting edits and legacy vectors.** The legacy decoder discards reply
  indices, and E1 saves the vector with the expected input hash. D162 can
  therefore re-embed unchanged passages on the first fitting edit of an
  E1 READY predecessor. Only candidates with verified mappings and delivery
  can donate. The release regression counts paid inputs, tests an older
  verified match behind an unverified newer row, and checks reuse of the
  newly verified vector on the next matching edit.
  [Decoder](../../src/rememberstack/adapters/openrouter.py#L1052-L1077) ·
  [E1 persistence](../../src/rememberstack/workers/e1.py#L502-L572) ·
  [Legacy donor order](../../src/rememberstack/spine/chunk_catalog.py#L422-L439)
- **Reproducible offline check.** The implementation PR records the
  pre-D162 representation inventory, baseline SHA-256 digests and exact
  restore or extraction and check commands. The oracle reads the saved
  grids. The legacy allocator alone receives the saved UUIDs, in order.
  The implementation PR also updates the E1 acceptance YAML.
  [LoCoMo restore](../../benchmarks/locomo/sharding/README.md#4-restore-one-conversation) ·
  [D162 release checks](../../plan/designs/split_oversized_chunks_design.md#5-offline-benchmark-and-release-checks) ·
  [E1 acceptance YAML](../../plan/implementation_evals/eval_checks/e1_chunks_whole_blocks_no_overlap.yaml#L13-L23)
- **SQL rollout and rollback.** The release rehearsal compares
  previous-release ordinary SQL and active saved queries on the expanded
  schema, verifies the setup manifest transition after old readers drain,
  and checks the publication gate before enrollment. Pre-activation
  rollback after manifest setup uses the registry publisher and seed
  directly because the old setup starts with Alembic. After activation,
  compatible binaries enforce the gate throughout recovery. A return to
  incompatible binaries uses the verified pre-activation restore.
  [Setup](../../src/rememberstack/profiles/selfhost.py#L1169-L1176) ·
  [Registry transaction](../../src/rememberstack/surfaces/query_sandbox/saved_queries.py#L1846-L1896) ·
  [D160 and D161](../../plan/designs/packaging_distribution_design.md#61-migration-compatibility)

## Nit

- **Body verification.** The body path verifies the stored embedding-text
  hash from the normalized P1 body and D80 header. The design now explains
  both source-content hashes, ordered block hashes for whole-block runs and
  tagged exact-byte hashes for partial blocks or gaps.
  [Body verifier](../../src/rememberstack/surfaces/query_sandbox/nomination.py#L784-L821) ·
  [Query-space design](../../plan/designs/open_query_space_design.md#34-sql-callable-functions)

## Verification

Provider-free probes reproduced the heading-change grounding failure and
the legacy decoder's acceptance of duplicate reply indices. The combined
PostgreSQL 18 run had 205 passes, two skips, the expected D122 window
failure, and a `facts_by_tag` tombstone assertion failure. All 65
saved-query tests passed on a fresh database, including that assertion.
The revision passed 29 local link and anchor checks and `git diff --check`.
The frozen-grid comparison, hosted qualification and rollout rehearsal
are implementation release checks.
