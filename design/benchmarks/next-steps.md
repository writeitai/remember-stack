# What needs to be done

Ranked by expected leverage per unit of risk, each item with its evidence.
Ordering rationale: the miss taxonomy says retrieval reach dominates, so
retrieval/answer-side levers come before any extraction-policy change —
and every extraction change forces re-ingestion of every store.

## Tier 1 — retrieval/answer side (no re-ingestion, no engine policy risk)

1. **Chunk-neighborhood expansion at answer time.** Let the agent (or the
   tool) pull the chunk text surrounding top-ranked claims. Evidence: gold
   answers repeatedly sat in the same chunk as a retrieved claim
   (conv-26 qa/0083: the realization sentence was adjacent to the rank-1
   hit). Attacks the ~60% Unknown bucket directly.
2. **Retrieval dedup + widened/multi-query retrieval.** k=10 windows were
   observed wasting half their slots on near-identical claims (five copies
   of "participants are Caroline and Melanie"). Dedup by claim text,
   consider k>10 and 2–3 query reformulations per question; ~33% of misses
   had zero claims from the gold session.
3. **Answer-policy calibration ("Unknown" vs attempt).** The judge scores
   Unknown as wrong; with partial evidence an attempt strictly dominates.
   Needs a deliberate protocol decision (it changes answer-agent prompt →
   new fingerprint) — cheap to test on the smoke tier.
4. **Teach the agent its multi-hop tools.** Cat-1 is 19% and traces show
   only claims_verbatim usage; graph/relationship recipes exist in the
   catalog but are never called. Prompt-side nudge first, tool-description
   pass second.

## Tier 2 — harness correctness/ergonomics

**Shipped 2026-07-31 in `RS-LoCoMo-Full-v8` — answer-stage correctness.**
The conv-47 v7 re-score was 91/150. The six-word answer cap produced 7 outright
`answer_invalid_response` failures, while 19 judged misses had gold answers
longer than six words; v8 now permits the shortest complete entity/value phrase
up to twenty words. Separately, 23/150 questions (about 15%) ended as
`answer_reader` with `reader_attempts: 0`, no tool calls, and a first-step
“completion content is not JSON” failure against prompts averaging about 22,800
input tokens. V8 extends the existing shared two-retry allowance to that first
step, charging every attempt to the normal call and cost budgets while leaving
plain provider outages terminal.

**Shipped 2026-08-03 in `RS-LoCoMo-Full-v9` — Batch B retrieval and optional
answer cap.** The ordinary catalog now exposes entity-anchored document and
claim retrieval, stamped claim-window retrieval, and current chunk neighbors.
The answer cap is a persisted/fingerprinted protocol field and is off for both
stock v9 entries; the qualitative shortest-complete-answer instruction remains.
V9 results are not comparable to v8 or earlier.

5. **Derive the protocol fingerprint from identity fields only** (drop
   `repository_revision` from the hash, keep it recorded). Evidence: the
   sharded run could not be officially merged with the sequential half
   despite byte-identical protocol identity (runbook §6 workaround).
6. **Judge tolerance review.** Collect the judged-wrong-but-semantically-
   right pairs ("Caroline is transgender." / "Transgender woman") and
   decide whether the judge prompt needs an explicit equivalence rule.
   Protocol-identity change; measure on smoke first.
7. **Dead-letter replay inside the sharding kit.** The kit aborts on
   dead-letter (correct default); the proven wrapper behavior (bounded ops
   replay + resume, 3 rounds) should become a kit flag so full runs don't
   need external babysitting. Same for partial-checkpoint clean-restart of
   single-sample shard dirs.

## Tier 3 — extraction side (needs corpus-wide sizing first, then re-ingest)

8. **#177 selection `generic`/`opinion` drops.** Size across all ten
   dumped ledgers (join drops against gold-evidence turns) before touching
   the rubric; the attributed-realization exemption is drafted in the
   issue. Also revisit the `opinion` reason — feelings are answers in
   LoCoMo.
9. **#174 glm reasoning-bleed salvage.** Still the root cause of nearly
   every dead-letter row; effort=none reduced but did not eliminate it.
10. **Dated-claim coverage** is 33%; cat-2 misses include undated claims
    that had anchors available. Measure which anchor forms still fail
    before another prompt round.

## Standing infra/process items

- Keep the benchmark-host snapshot current after each engine release
  (clones inherit the stack, `.env`, and dataset from it).
- Next full run: shard from the start (all 10 conversations parallel,
  ~2.5 h wall-clock) now that the server quota allows it.
- Per-conversation stores from this run are preserved for retrieval
  experiments — restoring a dump + re-embedding + projections rebuilds a
  queryable store without re-extraction.
