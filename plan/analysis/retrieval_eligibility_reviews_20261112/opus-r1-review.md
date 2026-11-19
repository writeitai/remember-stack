**Verdict: CHANGES REQUIRED**

The direction is right, and I'd approve its shape:
- Keep D142 scoped exact ranking and broad index nomination.
- Compute eligibility once per statement as a set, inside the operation's existing snapshot.
- Split facts into "has visible historical evidence" and "both endpoints are visible entities".
- Change no ingestion or reuse keys (D56), add no reranker, no fallback and no LoCoMo-specific logic.

But several contracts are too loose to implement safely. Some points as written could also lead an implementer to change who or what is visible.

I worked read-only and verified the claims against the source at `32b2a5d2d`. That covered `p9_45_0066` (the current `entities_current`, `v_graph_survivor` and `facts_visible_history`), `p9_04_0025` (`v_memory_fact_visible` and its consumers), `p9_14_0035` (`v_memory_mention_current_content` and `mentions_live`), `p9_09_0030`, `query_engine.py`, `postgres_p1.py` and `source_definitions.py`. I also read the captured actual plan `conv26/scoped-claims_and_sources_context/plan-004.json.gz`.

---

## Blocking findings

### B1. §4.1 presents the current entity-visibility design as if it were new, and leaves out the measured defect and two invariants

**Location:** proposal §4.1, lines 83–104.

**What is wrong.** The live `entities_current` (from `p9_45_0066`, which implements D149) already does what §4.1 describes:
- it walks absorbed members backwards along merge redirects (`merged_into`);
- it starts from each member's unsuperseded resolution decisions;
- it keeps only the latest decision per mention (`ORDER BY decided_at DESC, decision_id DESC LIMIT 1`).

The measured fault is in how PostgreSQL executes it. In plan-004:
- the planner joins 3,701 decisions with all 19 visible versions, giving 70,301 pairs;
- for each pair it probes every chunk partition by `(deployment_id, doc_id)`;
- it joins the mention's own `chunk_id` last, through `ix_mentions_doc`.

A reader who doesn't know this may rewrite the semantics rather than the physical execution.

**Required amendments:**
1. State the current definition and the measured cause, citing plan-004. State that membership is unchanged and only the physical shape changes.
2. Define the probe chain explicitly: decision → mention by its own key → chunk by `chunk_id` (also checked against the mention's deployment and document) → version by `version_id` → reading by its key. Each step should be a correlated lookup boundary.
3. Make the document-entity bridge check a per-member probe through `ix_documents_entity`. Today the plan builds a hashed subplan over **every** live document in the deployment, which costs work proportional to all documents for each entity check at millions of documents.
4. Pin two invariants that are easy to break:
   - **Any ready reading counts.** Provenance accepts any *ready* reading (`representation.status = 'ready'`) of any non-deleted version of a live document — not only the version's *current* reading. Reusing `chunks_live` or `chunks_all_versions_live` (both require the current reading) would silently drop entities that are visible only through older versions.
   - **The latest decision is chosen across all of a mention's decisions.** It must be picked from **all** of the mention's unsuperseded decisions, not only those that name a member. Otherwise a mention whose latest decision moved to another entity would still count.
5. Note that no prototype of the fixed entity lookup exists. The single lookup still costs 1.31 s and 1,838,700 cached page reads. Treat §4.1 as a hypothesis that gate 3 must confirm. If it cannot be confirmed, return to design review rather than adjusting visibility rules.

### B2. The "bounded, candidate-driven" claim is not backed by actual index paths

**Location:** proposal §4.1–4.2, §8 and §9 gate 3.

**What is wrong:**
- **Chunk coverage has no index to start from.** `mentions` has no index on `chunk_id`; it only has `(deployment_id, doc_id)` and `(claim_id)`. Chunk coverage restricted to candidate chunk IDs therefore scans every mention of the candidate's document. For one very long document (a transcript or a book), that cost grows with the document's total mentions, not with the number of candidates.
- **ID probes touch every monthly partition.** `chunks`, `mentions` and `resolution_decisions` are partitioned by month on `created_at`, with primary keys `(id, created_at)`. Every lookup by ID probes every partition — plan-004 shows 10 probes per lookup — so work grows with the deployment's age. That is the "unrelated data multiplies the work" pattern §4.1 forbids.

**Required amendments:**
- List the index used by each probe in §4.1 and §4.2.
- Decide whether to add a read-path index such as `mentions (deployment_id, chunk_id)`. It has no D56 impact, but the build cost and locking at millions of rows must be stated.
- Extend gate 3 with:
  - one large document with many mentions in unrelated chunks;
  - many partitions;
  - millions of documents, including documents with entity bridges;
  - entities whose earliest decisions point at invisible versions.

### B3. §4.3 fact factoring is not specified concretely enough to implement

**Location:** proposal §4.3, lines 134–174.

**(a) The rules contradict each other as written.** The text requires all three of these:
- "one maintained SQL assembly contract shared by the public wrapper and native reads";
- no second hand-maintained fact-truth query;
- no runtime string replacement of the public view definitions.

The public views are defined as SQL strings inside migrations that may never be edited, and `source_definitions.py` reads those strings to build the manifest. The runtime queries are Python strings. As written, no implementation can satisfy all three rules.

Required: specify each piece.
- Name the new private helper, for example `v_memory_fact_source_qualified`, and list its exact columns:
  - survivor endpoints, through the existing inner join to `v_memory_entity_survivor`, so facts whose merge chain is cyclic or dangling stay absent;
  - `valid_precision`;
  - D54 support counts through a left lateral join that PostgreSQL can skip when unrequested;
  - `support_state_current`.
- Define `v_memory_fact_visible` as that helper plus the endpoint rule: the subject must be in `entities_current`, and for relations the object must be too.
- State that the runtime builder reads only the helper and `entities_current`. The endpoint rule should be the only runtime-written truth, and a test must pin it against the migration's definition.

**(b) The consumers are not listed.** `v_memory_fact_visible` feeds:
- `facts_visible_history`, `fact_claim_evidence_live` and `evidence_lineage`;
- `v_memory_page_citation_visible`;
- `changes_visible`, which materializes the whole deployment.

On the runtime side, the affected call sites are `_CONFIRM_FACTS_CONTEXT_BY_KIND`, `_FACT_AUTHORITY_JOIN` (used by both scoped nomination and `_fact_loop` confirmation), `_FACTS_CONTEXT_CONTRADICTION_MEMBERS`, `graph_queries.py` and the SQL sandbox's `nomination.py`. Say which of these move to the bounded builder, and state that each public view keeps the same rows.

**(c) The helper's ACL and registration are not stated.** Add the helper to `AUTHORIZATION_HELPER_VIEWS` so its definition is hashed into the query-space manifest. Revoke it from PUBLIC and from the query roles, and set its owner to `rememberstack_view_owner`.

**(d) There is no evidence yet for facts.** Both the native fact run and the per-fact fenced prototype exceeded 20 s. The scoped budget (`ENTITY_WORK_BUDGET` = 50,000) counts raw fact rows, but the new cost is source qualification per fact plus a provenance check per distinct endpoint. State what happens when a scope is inside the budget but cannot finish — a deadline boundary, or a recalibrated budget — and add that worst case to the gates.

### B4. Gate 1 has no oracle after the migration

**Location:** proposal §9 gate 1, together with §6 ("no old SQL path").

Once the migration replaces the views, the "original reference" no longer exists in the database being tested.

**Required:**
- Build the oracle from the pre-change definitions: the constants in `p9_45_0066`, `p9_04_0025` and `p9_14_0035`, installed into a test-only schema, plus copies of today's runtime statements kept in test code.
- Compare complete rows on randomized fixtures.
- Run retained-store comparisons only on isolated copies, because the retained stores are read-only.

### B5. Gate 2's case matrix is missing several cases

**Location:** proposal §9 gate 2.

**Add:**
- merge cycles and dangling redirects;
- un-merges;
- isolation between two deployments, including a redirect chain that passes through another deployment;
- several unsuperseded decisions on one mention, including timestamp ties broken by `decision_id`, and a latest decision that has moved to a non-member;
- a mention whose `doc_id` differs from its chunk's `doc_id`;
- readings that are not ready, and readings that were replaced but are still ready;
- requested IDs that are merged-away, inactive or invisible;
- a mention whose claim is not visible, which makes `mentions_live.claim_id` null;
- chunks from a non-current version;
- D140 periodised claims whose origin version was deleted.

### B6. The planning corpus is not reconciled

Under the design-corpus rule, implementation must not proceed against a proposal alone.

**Required:**
- Name the binding sections that will be updated: `postgres_p1_search_projection_design.md` §8.3–8.5, plus the entity-visibility and query-space helper sections of `entity_identity_and_retrieval_design.md` and `open_query_space_design.md`.
- Add a decision entry, or an in-entry amendment to D149, and state what triggers adoption.

---

## Which gates are strict correctness and which are throughput

**Strict correctness (must pass before merge):**
- **Gate 1**, with the oracle from B4.
- **Gate 2**, with the cases from B5.
- **Gate 3 structural checks:**
  - no repeated work per candidate;
  - no growth with unrelated data or partitions;
  - unrequested counts absent from ID-only plans.
- **Gate 5 correctness:**
  - snapshot isolation;
  - no stale proofs;
  - cancellation is honoured;
  - no leaked connections;
  - early-stop accounting unchanged.
- **Gate 6.**
- **Gate 4**, as the condition for claiming the regression is fixed.

All of these are feasible. Snapshot sharing already exists through `_operation` and `_consistent_read`, which join the pool's read-only repeatable-read snapshot.

**Nice to have (record, don't threshold):**
- absolute latencies outside the reproduced stores;
- eight-reader CPU, memory and throughput;
- temporary-file usage.

---

## Nonblocking suggestions

1. **Sequencing.** In `plan/plans/`, not the design: land §4.1 and §4.2 first and measure, then do fact factoring. Factoring is still justified because a scoped search of up to 50,000 facts needs the subject's visibility computed once, but the measurements will calibrate it.
2. **Smallest safe fact change.** Add one private helper and redefine `v_memory_fact_visible` on top of it, keeping the existing per-kind `UNION` structure and the public column lists. Avoid any other public-view edits so the manifest changes as little as possible.
3. **Endpoint lookup shape.** Specify that endpoint visibility is a correlated per-endpoint lookup driven from a materialized set of distinct endpoints. A plain `IN (...)` join could let the planner expand `entities_current` across the whole deployment.
4. **D140 gate placement.** State explicitly that the D140 `fact_in_scope` evidence gate stays per candidate and is never shared across candidates.

---

## What is already safe

- **Snapshot, deadline and cancellation:** inherited unchanged from D142.
- **D56:** no paid keys change and nothing is re-ingested.
- **No hidden fallback:** no feature-flagged old path and no exact-search fallback.
- **Benchmark independence:** no benchmark-specific SQL. Updating the current LoCoMo manifest pins after a real query-space change is legitimate.
- **Claims and chunks coverage (§4.2):** a faithful refactoring of how `mentions_live` computes coverage, provided the claim-visibility condition carried in `mc.claim_id` is preserved and B2's chunk access path is resolved.

Once B1–B6 are amended, the implementation boundary — request-local eligibility shared as sets, under one snapshot — is sound.
