# Proposal: Observation adjudication efficiency (E3 write path)

**Status:** Open proposal — **not binding**, not implemented.  
**Date:** 2026-08-06  
**Owners / origin:** BEAM 100K smoke ops (DeepSeek hang → luna extract; long
`normalize_relations` tail on observation adjudication).  
**Binding design this sits under:**
[`plan/designs/observations_design.md`](../../plan/designs/observations_design.md)
(D43). Implementation today:
`src/rememberstack/spine/observation_adjudication.py`, invoked from
`src/rememberstack/workers/e3.py` (`NormalizeRelationsHandler`).

---

## 1. Problem

After claim extract, the E3 stage `normalize_relations` does two large pieces
of work for **one document version** in a **single sequential job**:

1. **Per claim:** one structured normalizer LLM call → relations (written
   immediately) and/or observation candidates (queued in memory by entity).
2. **Per entity with queued observations:** observation adjudication — block
   on the entity, rank open priors, cheap gates, then a small→frontier LLM
   ladder on the residue.

On large conversational documents (BEAM 100K smoke, ~4k claims), (1) finishes
in hours of linear claim work; (2) then becomes the **dominant wall-clock and
chat-LLM tail**, especially on **hub entities** (many observations about the
same subject, e.g. a generic "User" person node).

### What the write path does today (as implemented)

For each new assertion that is not free (exact statement match, first
observation on entity, or clear embedding novelty):

1. **Rank:** call the embedding API on  
   `NEW + every open observation.statement` on that entity, cosine in
   process, sort. **Not Lance** (see §5).
2. If max similarity &lt; `novelty_floor` (default `0.3`) → insert as new (no
   chat LLM).
3. Else for each of the top **`hub_top_k`** (default **5**) priors:  
   small-model pairwise verdict (`EXISTING` vs `NEW`); escalate to frontier
   if confidence &lt; `confidence_floor`. Stop on first decisive outcome
   (evidence / supersede / contradict); if all say `new`, insert.

Settings live on `ObservationSettings`
(`REMEMBERSTACK_OBS_*`): `small_model`, `frontier_model`, `embedding_model`,
`confidence_floor`, `supersede_margin`, `novelty_floor`, `hub_top_k`.

### Evidence from one BEAM 100K smoke (order of magnitude)

Snapshot during a long normalize run (not a final audit):

| Signal | Approx. |
| --- | --- |
| Claims | ~3,970 |
| Claim normalize LLM done | 3,970 / 3,970 |
| Observation-producing claims (resolve path) | ~1,500 |
| Obs claims with evidence landed vs still pending (mid-tail) | ~1,000 vs ~1,000 |
| Relations written | ~200 |
| Hub example | entity "User": ~90 observations, **~661** cost rows in ~22 min |
| Obs-phase cost mix | ~3k `small_model` verdicts, ~1.2k rank embeds, **0** frontier |
| Ingest ledger so far (same run, mid-normalize) | **~$1.5** total; ~half extract, ~half normalize |

Runbook already notes that relation-normalize is a long sequential tail at
hundreds of claims (`design/benchmarks/runbook.md`). Multi-thousand-claim
docs make that binding for ops.

### Why this is expensive (not "stuck")

- One `processing_state` row per document version → **one worker holds the
  lease**; scaling `worker-normalize-relations` does **not** shard one doc.
- Same-entity assertions are **ordered** (later must see earlier writes).
- Residue path can issue **up to `hub_top_k` chat calls per assertion**;
  hubs with many paraphrased statements often get `new` for several
  neighbors → pay near the full K.
- Rank **re-embeds the entire open set** on every residue assertion.

The job was observed still writing cost rows; the issue is **algorithmic
cost shape**, not an idle hang.

---

## 2. Non-negotiables (must not break)

From D43 / `observations_design.md`:

1. **Exhaustive entity block** in Postgres — membership is exact
   `subject_entity_id` + live rows. Ranking only **orders**; it must never
   hide a prior from the block.
2. **Fail-safe coexist** — supersede only on a positive match above margin
   with rationale; incomplete/weak compares must not cap windows.
3. **No-cap rule** for fixed-period measurements (semantic, not a typed
   column).
4. **Same-entity assertion order** within a document batch (later sees
   earlier open set).

Anything below that increases **duplicate coexistence** is usually safer
than anything that increases **wrong supersede**.

---

## 3. Operational knobs (not this proposal’s code track)

Documented so they are not confused with the algorithmic list. Available
without new design acceptance:

| Knob | Effect |
| --- | --- |
| `REMEMBERSTACK_OBS_SMALL_MODEL` | Direct $ / latency on verdict volume |
| `hub_top_k` | Max pairwise LLM compares per residue assertion |
| raise `novelty_floor` | More embed-only "clear novelty" adds; more near-dupes may coexist |
| raise `confidence_floor` | Fewer frontier escalations |
| raise `supersede_margin` | Fewer caps; more coexist |

These are tuning, not substitutes for the proposals in §4.

### What `hub_top_k` means (glossary)

After embedding-rank of **all** open observations, only the **top K most
similar** priors enter the LLM ladder. Each such prior is (up to) one
small-model call, plus optional frontier. Higher K → higher worst-case and
typical chat cost when many priors return `new`. Rank still sees the full
open set; K only caps **LLM** width.

---

## 4. Algorithmic proposals (real savings, code)

Each item is an **unchosen** option. They can be combined. Suggested
implementation order is in §7.

### B1. Cache observation embeddings (write-path rank)

**Problem.** `_rank` embeds `NEW + all open statements` on every residue
assertion via `model_provider.embed`. Priors on hubs are re-embedded
dozens of times. **Lance is not used** on this path today (§5).

**Proposal.**

1. **In-process cache** for the lifetime of a normalize / adjudicator
   session, keyed by `(embedding_model, embedding_version)` plus either:
   - `observation_id` for open rows, and/or
   - content hash of normalized statement text for NEW and for rows.
2. **`_rank`:** resolve hits from cache; **one** embed API call for the
   batch of misses; cosine sort as today.
3. **Write-through:** when a new observation is inserted after a rank that
   already embedded NEW, store that vector under the new id (and hash) so
   the next assertion on the same entity does not re-embed it as a prior.
4. **Optional warm** at the start of `add_observations`: embed all open
   candidates missing from cache in one batch.

**Phase 2 (optional durability):** Postgres side table
`observation_embedding_cache (observation_id, model, version, vector, …)`
or denormalized columns — for crash-resume and multi-worker entity shards.
Do **not** require Lance for write-path rank unless P1 ordering changes
(observations are often adjudicated **before** P1 projects them).

**Savings.** Embed API volume and rank latency: from roughly
O(assertions × open_set) toward O(unique statements + new asserts).  
**Does not** reduce pairwise verdict LLM count by itself.

**Risks.** Low if model/version are part of the key. Wrong cache across
model pins is the main footgun.

**Adoption trigger.** Any multi-thousand-claim ingest or hub-heavy domain;
or measured rank embed $ / latency dominating normalize.

---

### B2. Multi-candidate / batched verdict (fewer chat calls)

**Problem.** Residue path does up to `hub_top_k` **sequential pairwise**
small-model calls per assertion.

**Proposal.** One structured call: given NEW and the top-K prior
statements, return the best interaction (evidence / supersede /
contradict / new per prior, or a single chosen prior + outcome). Preserve
fail-safe: weak/ambiguous → coexist, never silent cap.

**Savings.** Up to ~K× fewer chat calls on hub residue (quality-bound).

**Risks.** Prompt and schema design; regression on supersede/contradict
eval gates (`plan/implementation_evals/…`, D43 eval surfaces). Needs
golden / adjudicator evals before binding.

**Adoption trigger.** After B1, if small_model verdict count still
dominates cost (as on the BEAM smoke).

---

### B3. Stronger free collapses before LLM

**Problem.** Design expects most volume to exit with **zero chat LLM**
(first mention, exact re-assert, clear novelty). Conversational paraphrase
sits in the middle band and burns small-model.

**Proposal (combinable):**

- Normalize statement text (whitespace, quotes, trivial unicode) so more
  **exact** evidence-collapses hit.
- High embedding similarity threshold (e.g. ≥ 0.92–0.95, tunable) →
  evidence-collapse **without** LLM when statements are near-identical.
- Keep supersede/contradict on the LLM (or multi-candidate) path only.

**Savings.** Large reduction in verdict calls if many near-dupes.

**Risks.** Over-collapse of distinct facts that are lexically similar.
Calibrate on obs evals; bias toward coexist if unsure (higher bar for
auto-evidence than for auto-supersede).

**Adoption trigger.** Measured fraction of residue pairs that human/eval
would call "same statement, rephrase."

---

### B4. Pre-dedupe inside the in-memory entity batch

**Problem.** One document can queue many near-duplicate observation
assertions on the same entity (extract + normalize paraphrase).

**Proposal.** Before `add_observations`, within
`observations_by_entity[entity_id]`:

- collapse exact duplicate statements (keep first claim id as primary,
  attach others as evidence-only if the adjudicator supports multi-claim
  evidence in one go, or sequential exact-match after first insert);
- optionally collapse ultra-high embed-sim duplicates with the same
  free-collapse rule as B3.

**Savings.** Fewer assertions entering the ladder; same entity batch
shrinks.

**Risks.** Low for exact dedupe. Near-dupe rules share B3 calibration.

**Adoption trigger.** Entity batches with high duplicate-statement rate
(inspect assertion lists on hubs).

---

### B5. Parallelize across entities (wall-clock)

**Problem.** Entity batches run **sequentially** in one job even though
locks are per entity (`pg_advisory` key `deployment:obs:{entity_id}`).

**Proposal.** After the claim loop (or after persisting pending
assertions — see B6), fan out **entity batches** to multiple workers or
async tasks. Same entity remains strictly ordered. Different entities
concurrent.

**Savings.** Wall-clock on multi-entity docs; **not** total LLM $ (same
work, more parallelism).

**Risks.** Provider rate limits; connection pool; must not parallelize
assertions within one entity.

**Adoption trigger.** Ops need faster drain of single large docs; provider
capacity allows higher concurrency.

---

### B6. Split stage: claim-normalize vs observation-adjudicate

**Problem.** One stage mixes linear claim normalize with the obs tail;
progress is opaque; crash mid-obs may force expensive replay policy;
cannot scale entity work without surgery.

**Proposal.** Separate pipeline stages (names illustrative):

1. `normalize_relations` (or rename): claim LLM + relation upsert +
   **persist pending observation assertions** (table or payload).
2. `adjudicate_observations` (work items per entity or per doc-entity):
   existing adjudicator only.

Aligns better with enum history (`adjudicate_observations` already exists
in older `pipeline_stage` lists) and with D43’s "one write path" living in
the adjudicator module.

**Savings.** Operational and architectural: retries, sharding (B5),
metrics ("entities left"), no re-normalize of 4k claims to finish obs.

**Risks.** Migration of processing_state / enqueue graph; careful
idempotency for pending assertion store.

**Adoption trigger.** Accepting multi-hour single-stage tails as a product
ops problem; or need for B5 in production.

---

### Upstream reducers (related, not pure adjudicator)

Not B1–B6 but affect the same bill:

- Tighter extract / fewer low-value claims.
- Better entity resolution (fewer mega-hubs from generic names).
- Normalize prompt: fewer junk observations on chat roles.

---

## 5. Design vs implementation gap (Lance)

**Binding design** (`observations_design.md` §3): hub narrowing ranks by
semantic similarity using **P1/Lance** over the observation label —
ordering only, block remains exhaustive.

**Implementation today:** live `model_provider.embed` over statement
strings + in-process cosine. **No Lance read on the adjudicate path.**

P1 (`workers/p1.py`) embeds observations into the facts index for
**retrieval**, often **after** graph write. Using Lance as the write-path
rank cache implies either:

- early projection at observation create time, or
- accepting that write-path cache is separate (B1 in-process / Postgres)
  and Lance remains retrieval-only.

**Proposal stance:** treat B1 (explicit write-path cache) as the first
efficiency move; reconciling design text with "Lance for rank" can be a
follow-up once early projection or dual-write is intentional — not a
blocker for memoization.

---

## 6. Explicit non-goals

- Replacing exhaustive SQL block with ANN-only candidate sets.
- Parallelizing assertions **within** one entity.
- Silent supersede without margin/rationale to "save tokens."
- Changing fail-safe coexist into auto-overwrite for speed.

---

## 7. Suggested sequencing (if adopted later)

| Order | Item | Why first |
| --- | --- | --- |
| 1 | **B1** embed cache | Low risk, immediate rank $ / latency; no eval semantics change |
| 2 | **B4** exact batch dedupe | Trivial, shrinks hubs |
| 3 | **B3** free near-dupe collapse | Needs calibration; large verdict savings |
| 4 | **B2** multi-candidate verdict | Needs eval; large chat savings |
| 5 | **B6** then **B5** | Stage split enables safe entity parallelism |

Operational knobs (§3) can ship anytime for smoke experiments without
accepting this proposal.

---

## 8. Adoption / rejection triggers

**Adopt (promote pieces into binding design + implementation) when:**

- Multi-hour obs tails or multi-dollar normalize bills are routine at
  target scales; and/or
- Measured cost mix shows rank embeds and/or pairwise small_model as
  dominant; and
- Eval gates for observation outcomes stay green for the chosen subset.

**Reject or defer when:**

- Typical deployments stay small (tens of claims, few hubs); knobs suffice;
- Product priority is elsewhere and smoke-only cost is acceptable.

**This document does not accept any of B1–B6.** It only parks them so they
are not lost in chat.

---

## 9. Implementation touchpoints (for a future implementer)

| Area | Path |
| --- | --- |
| Adjudicator | `src/rememberstack/spine/observation_adjudication.py` (`_rank`, `_ladder`, `add_observations`) |
| E3 handler / batching | `src/rememberstack/workers/e3.py` |
| Settings | `ObservationSettings` / `REMEMBERSTACK_OBS_*` |
| P1 projection (retrieval, not write rank today) | `src/rememberstack/workers/p1.py`, fact index |
| Binding semantics | `plan/designs/observations_design.md` §3 |
| Evals | `plan/implementation_evals/` observation / E3 checks |

---

## 10. Open questions

1. Should write-path vectors ever share storage with P1/Lance, or stay a
   separate cache with an explicit model pin?
2. What free-collapse similarity threshold keeps D43 evals green?
3. Is stage split (B6) worth the orchestration cost before or after embed
   cache (B1)?
4. For multi-candidate verdicts (B2), is one shared small model enough or
   do we keep pairwise as a fallback when confidence is low?

---

## 11. References

- Code: `observation_adjudication.py`, `e3.py` (as of proposal date).
- Design: `plan/designs/observations_design.md` (D43).
- Ops: `design/benchmarks/runbook.md` (normalize tail / worker scale notes).
- Smoke context: BEAM 100K ingest, chat model `openai/gpt-5.6-luna` for
  extract/normalize after DeepSeek/BaseTen rate-limit issues; observation
  small_model luna; embeddings `qwen/qwen3-embedding-8b`.
