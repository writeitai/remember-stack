# Analysis: claim-level E3 normalize fan-out

**Status:** non-binding analysis  
**Date:** 2026-08-10  
**Related:** D84 (chunk extract), D86 (unknown type gate), D12/D67 (ledger work truth)  
**Incident / driver:** BEAM 1M on `umc-beam-bench-01` — after Claimify (~15k
claims) and D86 deploy, `normalize_relations` remains a **single version-level
job** walking claims serially (~600–700 claims/h). Scaling
`worker-normalize-relations` does not help (one lease).

## 1. Problem

### 1.1 What is slow

`NormalizeRelationsHandler.handle` (version grain) loops every accepted claim
of a representation, serially:

1. LLM normalize (temp 0; optional D86 type-gate retry)
2. Resolve/mint entities (lemma lock; optional T3/T4)
3. Upsert relations + buffer observations
4. After **all** claims: observation adjudicator batches, then enqueue
   `adjudicate_supersession` + `embed_claim`

Measured on BEAM attempt 5 (order-of-magnitude): normalize `a1` p50 ~2 s,
resolve avg ~3 s → multi-hour/overnight wall clock for 15k claims.

### 1.2 Why scaling workers fails

The work ledger leases **rows**. One `normalize_relations` row with
`target_kind=document_version` means one holder. Extra replicas idle.

D84 fixed the same failure mode for extract by changing **ledger grain** to
chunk. Normalize did not get that treatment (explicitly deferred in D86).

### 1.3 What is *not* the problem

- D86 correctness (retry-then-drop) — orthogonal; keeps working inside each
  claim job.
- “Wrong supersession order if claims finish out of order” — only a problem if
  supersession is run **incrementally without a version barrier**. Fact evidence
  attach is commutative; supersession should use **claim asserted_at** and a
  complete relation set.

## 2. Alternatives

| Option | Verdict | Why |
| --- | --- | --- |
| Scale normalize workers only | Reject | No second lease on a single version job |
| In-process thread/async pool inside one job | Reject as sole v1 | Harder accounting, partial commit, weaker ops signal; still one ledger row |
| Batch N claims per LLM call | Reject v1 | Quality/schema risk; changes Claimify-adjacent contracts |
| Per-**chunk** normalize jobs | Viable v1.5 | Fewer rows (~chunks not claims); less parallelism on claim-heavy chunks |
| Per-**claim** normalize jobs | **Chosen v1** | Max parallelism; `ProcessingTarget.CLAIM` already exists; cost keys already `normalize:{claim_id}:aN` |
| Entity-sharded normalize | Reject v1 | Cross-claim coupling, different design |
| FIFO queue for “document order” correctness | Reject | Latency variance + multi-worker ⇒ reorder; not a safety property |

## 3. Continuous ingestion

Barrier must be **per document version** (expected claim id set fixed when that
version’s extract barrier fires), not global “all claims in deployment.”

| Continuous ingest scenario | Behavior |
| --- | --- |
| Many docs over days | Independent fan-out trees; workers multiplex |
| New version of same lineage | New expected set; does not enlarge in-flight version’s set |
| One claim DLQ on doc A | Only A’s barrier holds; B continues |
| “Ingestion never ends” | No global wait; readiness stays version-scoped |

Appending claims to a **closed** version without a new version id is out of
model (immutable representation). New text ⇒ new version.

## 4. Fact layer interaction

| Layer | Parallel claim normalize | Correctness mechanism |
| --- | --- | --- |
| Claims | Immutable | No supersession of claim rows |
| Relation evidence | Concurrent attach OK | SPO upsert; `(relation_id, claim_id)` ON CONFLICT DO NOTHING; recount |
| Entity resolve/mint | Concurrent OK under lock | Lemma advisory lock; residual cross-surface dupes = existing merge track |
| Observations | Concurrent OK under lock | D43 entity lock on write (per claim job) |
| Relation supersession | **After version barrier** | Version-scoped load of relations/evidence; asserted_at in prompts |
| Validity / reconcile | After supersession chain | Unchanged pipeline ordering relative to “normalize complete” |

**Do not** depend on queue FIFO for adjudication order.

## 5. Residual product choices (recommended)

1. **Mint type:** first-under-lemma-lock wins (status quo); no doc-order typing in v1.  
2. **Observations:** D43 is order-sensitive today; v1 uses **post-barrier ordered flush**
   by `asserted_at` (entity lock alone ≠ commutativity). Parallel claim jobs may
   only stage candidates.  
3. **Supersession payload:** post-barrier **version-scoped** adjudicate with
   relations evidenced by expected origin claims (not worker-local `relation_ids`;
   not creation-time windows).  
4. **Partial failure:** strict barrier — every expected claim job `status=succeeded`;
   soft assertion drops inside a job still count as success; DLQ blocks readiness.  
5. **Fan-out durability:** full expected set inserted in the extract-barrier handoff
   transaction (no incomplete coordinator success).  
6. **Barrier races:** mandatory advisory lock on complete+barrier (landed D84 pattern).

## 6. Costs

| Dimension | Effect |
| --- | --- |
| Model $ | ~same total tokens; higher concurrency may hit provider rate limits |
| Postgres | O(claims) processing rows per version (~15k BEAM); monitor bloat |
| Wall clock | Near-linear with normalize worker count until rate limits |
| Ops signal | `stage=normalize_relations` pending/running ≈ unfinished **claims** |

## 7. Relationship to D84 / D86

- **D84:** pattern template (fan-out + atomic complete+barrier + legacy coordinator).  
- **D86:** claim-level behavior **inside** each job; fan-out was deferred — this analysis un-defers it as its own decision.  
- Extract barrier currently enqueues **one** version normalize; this design changes that enqueue to **N claim jobs** (or coordinator that fans out).

## 8. Open implementation details (not product forks)

- Enqueue batching for 15k claim rows in one transaction vs chunked batches.  
- Exact readiness SQL update for claim-grain normalize (mirror D84 readiness work).  
- Whether stage stays named `normalize_relations` with `target_kind=claim` (preferred for less enum churn) vs a new stage name.

## 9. Conclusion

Adopt **claim-level ledger fan-out** for E3 with a **version-scoped strict
barrier**, post-barrier relation supersession, in-job observation adjudication
under entity lock, and D86 unchanged. Continuous multi-doc ingest is compatible
when the expected claim set is fixed per closed version.
