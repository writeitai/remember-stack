# Verdict: APPROVED

Round 3 of the review of `plan/proposals/shared_retrieval_eligibility.md` at `431900b1d` is done. All four round-2 blockers are fixed, and I checked each fix against the source. The new source evidence turned up no new correctness gap, so nothing in the design is left blocking.

I did not run the Python snippet at the end of your message. It starts another `claude` process, and the brief rules out creating agents. Instead I did the review in this session, read-only: no edits, no agents, no model, provider or database calls. This review is not committed. You may want to store it as `plan/analysis/retrieval_eligibility_reviews_20261112/opus-r3-review.md`.

## Round-2 blockers

**B1: the D140 fact authority. Resolved.**
- `p9_38_0059_d140_effective_time.py:1172-1214` rewrites `v_memory_fact_visible` (from the `p9_04_0025` text) and `v_memory_fact_claim_live` (from the `p9_09_0030` text) by string replacement. It joins the evidence to `claims_visible_history` UNION ALL `v_memory_claim_carried_periodised`, matched on deployment, claim and `doc_id`.
- No later migration touches these two views. `p9_45_0066` only rewrites `facts_visible_history` on top of them, so this is still the definition at the current head (`p9_46_0067`).
- §4.3 now writes that predicate out in full and keeps the widened `fact_claim_live`.
- Gate 1 builds the oracle from a real database migrated to `p9_46_0067`. It copies view definitions only mechanically from `pg_get_viewdef`, and includes facts whose only evidence is a carried periodised claim.
- §6 records the manifest gap correctly. `source_definitions.py:181-196` hashes the pre-D140 text and leaves out the carried-claim helper.

**B2: the entity-driven scope mode. Resolved.** I checked that the reverse path returns the same rows as the current filter in `postgres_p1.py:843-858`:
- **The current filter.** It matches a `mentions_live` row when its `resolved_entity_id` is in the request. That ID is set only when the survivor of the mention's latest decision passes `entities_current` (`p9_14_0035:179-229`).
- **The reverse path.** It starts from requested survivors that pass `entities_current`. It walks back to their merge members (`_SURVIVOR_SCOPE_BODY`), keeps member decisions that are the mention's latest decision overall, then reads the current-content row for that mention.
- **Why they match.** The latest decision's entity is a reverse member of the survivor exactly when its forward survivor is that survivor. A cycle or dangling redirect can't be reached backwards from an active terminal entity. A redirect through another deployment is impossible, because a composite foreign key forces `(deployment_id, merged_into)` into the same deployment (`p0_02_0003:34`).
- **Budget and coverage.** The rows counted before grouping, including the non-null claim-ID condition, are the rows the current budget counts.
- §4.2 names all four consumers: `_run_testimony`, `nominate_testimony_scored`, `_entity_tiers` and the budget helper. Gate 3 adds the case with many unrelated current mentions.

**B3: partition cost. Resolved.**
- §4.1 says the UUIDv7 pruning promised in D23 is not implemented (writers mint `uuid4`) and bans guessed time bounds.
- §10 reconciles `postgres_schema_design.md` (lines 84-88 and §12 at 2684+), where that promise lives.
- Gate 3 requires 120 monthly partitions plus the default one. The ID-minting and timestamp columns are unchanged.

**B4: schema-head and catalog pins. Resolved.**
- §6 and gate 6 name `catalog_contract.py`'s `EXPECTED_INDEXES` (`:195`) and `EXPECTED_VIEWS` (`:354`).
- They also name the head pins in `storage_capture_guard.py:82,145,273` and `storage_capture_catalog.py:135`.
- The D152 capture-guard and allocation checks are re-run at the new head, with no fallback to the old head.

## Why the boundary is safe

- **One definition of fact truth.** A single private, source-qualified fact helper holds all historical, D140 and count logic. It is written once as literal migration DDL.
- **One small runtime rule.** Runtime code assembles only the subject/object `entities_current` check, and the oracle tests it against the real `p9_46_0067` database.
- **Eligibility is shared only within one read.** It is a set of IDs reused inside one snapshot and one statement, with no cache, flag or persistent projection, so deletion, merge and version changes show up on the next snapshot.
- **No paid reprocessing (D56).** Nothing changes in ingestion, extraction or embedding, or in any reuse key or minted ID.
- **No hidden paths.** There is no legacy path, fallback, reranker or benchmark-specific logic.
- **Clear gates.** The required checks are gates 1, 2 and 6, the structural part of gate 3 in both directions, gate 4's native completion, and gate 5's isolation and cancellation. Latency, eight-reader CPU and memory, spill, WAL and index build time are recorded observations, not thresholds.

## Nonblocking suggestions (for the implementation PR)

1. **The claims `entity_id` filter.** `_claim_filters` in `postgres_p1.py:2106-2112` builds a per-candidate correlated `EXISTS … mentions_live … resolved_entity_id = :filter_entity_id`. That re-runs `entities_current` for each candidate, the same defect class as the measured one. It is not a correctness issue, but it should go through the shared visible-entity set or be listed explicitly in §4.3's consumer table and in gate 3.
2. **Where to register the new helper.** Add it to the tuple in `query_space/source_definitions.py:98`, or to a new tuple exported by the new migration. Never edit the tuples in `p9_04_0025` or `p9_09_0030`: `p9_04_0025:918` loops over its tuple to revoke grants, and the new helper doesn't exist yet at that revision.
3. **How to hash the widened `v_memory_fact_claim_live`.**
   - Have the new migration emit it as a literal `CREATE OR REPLACE` with the same body, so the manifest hashes literal text rather than `_fact_authority_views()` output.
   - Add a test comparing every registered helper's hashed definition with `pg_get_viewdef` from a migrated database. That closes the kind of drift behind B1.
4. **Test head pins.** `src/tests/database_reset.py:55` and the head assertions in `src/tests/spine/test_migrations.py` (`:152`, `:694` and others) also pin `p9_46_0067`. Update the head pins, but keep tests that deliberately upgrade to that exact revision.
