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
