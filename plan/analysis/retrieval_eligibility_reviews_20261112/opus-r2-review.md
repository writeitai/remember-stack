# Opus round-2 review of the shared retrieval eligibility proposal (`085b06869`): CHANGES REQUIRED

Most of round 1 is resolved: B1, B5 and B6 are done, and B2 and B3 mostly are. Four blockers remain. Each needs a wording or contract amendment in the proposal, not a different architecture. Three of them are gaps neither round caught, and one comes from a mistake in my own round-1 review.

I worked read-only: no files edited, no agents, no provider or database calls. The review is not stored in the repo yet; you may want to commit it next to `opus-r1-review.md`.

## B2 partition tradeoff: accepted

I explicitly accept the tradeoff as scoped. Monthly-partitioned ID lookups (`mentions`, `chunks`, `resolution_decisions`) may visit every retained partition, so their cost grows linearly with the number of partitions. The structural guarantee is "no multiplication by unrelated rows", not "constant cost however many partitions exist".

That cost can't be removed without new state anyway:
- Decisions don't store their mention's `created_at`, and mentions don't store their chunk's.
- IDs are random `uuid4` values, so a lookup can't work out which month's partition holds a row from the ID alone (`workers/e1.py:703` chunks, `spine/resolver.py:1052` mentions, `workers/e2.py:1930` and `spine/resolver.py:247` decisions).

Acceptance depends on blocker 3 below.

## Blocking findings

### 1. The fact authority and the oracle are taken from the wrong migration

**What's wrong.** The live `v_memory_fact_visible` is not the `p9_04_0025` text:
- D140's migration rewrites it by string replacement (`p9_38_0059_d140_effective_time.py:1184-1214`, applied at `:1587-1588`).
- Visible-claim evidence now comes from `claims_visible_history` combined with `v_memory_claim_carried_periodised`. That covers periodised claims still carried by a live version after their origin version was deleted.
- `v_memory_fact_claim_live`, which feeds the D54 counts, is widened the same way.

Two more problems compound this:
- `source_definitions.py:181-196` still reads the pre-D140 `p9_04_0025` text, so the manifest currently hashes an outdated definition.
- `v_memory_claim_carried_periodised` is not a registered helper.

**Why it matters.** Gate 1 names `p9_45_0066`, `p9_04_0025` and `p9_14_0035` as the oracle — the list I gave in round 1. An implementer who copies "the existing evidence predicate" from those constants would silently drop D140 carried evidence, and an oracle built from the same constants would agree. That is a visibility regression the tests would not catch.

**Required amendments:**
- **§4.3:** "Each arm's evidence predicate is the head `p9_46_0067` predicate, written as a literal in the new migration: `relation_evidence`/`observation_evidence` joined to (`claims_visible_history` UNION ALL `v_memory_claim_carried_periodised`). It is never derived by string replacement. `v_memory_fact_claim_live` keeps its `p9_38_0059` definition."
- **Gate 1:** "The oracle is a dedicated database built from this repository and migrated to `p9_46_0067`. A test-schema copy is allowed only if generated mechanically from that database's `pg_get_viewdef` output, never from hand-picked migration constants. Fixtures include facts whose only evidence is a carried periodised claim."
- **§6:** state the existing manifest gap. Then either register `v_memory_claim_carried_periodised` and the widened `v_memory_fact_claim_live` text as hashed dependencies (recommended), or record the gap as known. Don't claim complete hashing otherwise.

### 2. The entity-driven claim/chunk scope is missing (millions-of-rows path)

**What's wrong.** §4.2 defines coverage only for candidate IDs (hydration). Scoped nomination runs in the other direction:
- `postgres_p1.py:843-858` filters `mentions_live` on `resolved_entity_id = ANY(:entity_ids)`.
- The budget check (`_require_entity_budget`) and the coverage query (`_entity_tiers`, `:2503-2506`) both read that filter.
- `resolved_entity_id` comes from the latest-decision lookup plus the merge-survivor mapping, so no index can drive it. Every current mention in the deployment gets evaluated, and `LIMIT budget+1` doesn't bound that work.

It measured 0.04 s only because LoCoMo is tiny. Gate 3's "no multiplication by unrelated rows" would fail here, and no approved design would cover the fix.

**Required amendment (add to §4.2):** "Entity-driven scope mode. Path:
1. requested visible survivors;
2. reverse merge members (the existing `_SURVIVOR_SCOPE_BODY`);
3. `ix_resdec_entity_live`;
4. check that the decision is the mention's latest unsuperseded decision across all of that mention's decisions (`ix_resdec_live`);
5. the mention by primary key;
6. its current-content coordinate (current chunk and document, ready reading, nullable visible-claim gate);
7. group by claim or chunk ID.

The budget counts rows of this same relation. The rows must equal the current `mentions_live` filter, checked by gate 1. Gate 3 adds a deployment with many current mentions unrelated to the requested entity."

### 3. The accepted partition cost contradicts the binding schema design

**What's wrong.** `postgres_schema_design.md` §12 and line 84–88 say IDs are UUIDv7 and that the data-access layer prunes ID lookups with an ID-derived time bound. The code mints `uuid4`, so that mechanism doesn't exist.

**Required amendments:**
- **§4.1:** "The D23 §12 UUIDv7 time-bound pruning is not implemented (IDs are `uuid4`) and this design does not rely on it. Authority reads must never add a heuristic or correlated time bound, because a wrong bound silently hides evidence."
- **§10:** add `postgres_schema_design.md` §12 to the reconciliation list, so the binding design states the real fan-out behaviour.
- **Gate 3:** run at a realistic partition count (for example 120 monthly partitions plus the default one) and report probes per lookup.

### 4. Schema-head and catalog pins are not named

**What's wrong.** The new migration and index change the schema head, but these places are pinned to the current one:
- `catalog_contract.py` `EXPECTED_INDEXES` and `EXPECTED_VIEWS` have to list the new index and helper.
- The D152 storage-capture primitives refuse to run at any head other than `p9_46_0067` (`storage_capture_guard.py:82,145,273`; `storage_capture_catalog.py:135`).

**Required amendment (§6 and gate 6):** list both, and require D152 capture-guard re-qualification at the new head. Note that retained index allocation changes with the new index.

## Round-1 findings

| Round-1 finding | Status |
| --- | --- |
| B1 (entity-visibility execution) | Resolved. I checked the probe chain against `p9_45_0066:67-118` and every index name in the migrations. "Any ready reading" and "latest decision across all decisions" are pinned correctly. |
| B2 (index paths and migration) | Resolved, apart from blocker 3. `ix_mentions_deployment_chunk` and the leaf-build/attach procedure with a session lock are sound. |
| B3 (fact factoring) | The contract is implementable apart from blocker 1. There is no circular dependency: the D54 lineage helpers don't read `v_memory_fact_visible`. Appending columns doesn't alter consumers that select every column (`*`), because PostgreSQL fixes those column lists when the consumer view is created. The 18 columns match today's names, order and types. |
| B4 (oracle) | Only the source of the oracle is wrong (blocker 1). |
| B5 (case matrix) | Resolved. |
| B6 (corpus reconciliation) | Resolved, plus blocker 3's §12 reconciliation. |

The things you asked me to confirm also hold: no paid-ingestion or reuse-key changes (D56), no fallback or legacy path, no new reranker, nothing LoCoMo-specific. Snapshot, deadline and ACL handling are inherited correctly.

## Gates

**Strict correctness (must pass before merge):**
- gates 1, 2 and 6;
- gate 3's structural checks, including the new entity-direction case;
- gate 5's isolation, cancellation and accounting checks;
- gate 4 completion, as the condition for claiming the regression is fixed.

All are feasible.

**Recorded observations, not thresholds:**
- absolute latency;
- eight-reader CPU and memory;
- spill to temporary files;
- index build time and WAL volume;
- partition planning overhead.

## Nonblocking suggestions

1. Offline (`--sql`) migration mode: refuse it for this revision, or emit the autocommit blocks explicitly.
2. Reword "declare the parent valid": PostgreSQL marks the parent index valid only once every partition has an attached index. Verify that through `pg_index.indisvalid`.
3. The new migration needs to write `facts_visible_history`'s comments, including every column comment, as literals; `source_definitions` rejects a published view without them.
4. Sequencing in `plan/plans/`: entity probes and coverage first (both directions), then fact factoring.

Once these four amendments are made I expect to approve without further design changes. The boundary is still sound: request-local eligibility shared as sets of IDs inside one snapshot, one source-qualified fact authority, and only the endpoint conjunction assembled at runtime and tested for parity.
