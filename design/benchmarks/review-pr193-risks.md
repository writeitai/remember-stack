# PR #193 (hybrid retrieval) — post-merge review risks

Analysis, 2026-07-31. Two independent post-merge reviews (Claude, Grok-4.5)
of "Add hybrid claim and live-source retrieval" (#193, merged as `518f9d2`).
Both reviews found **no correctness hole**: D48 confirmation is not
bypassable through the new chunks channel (current-version/tombstone/ready
filtering happens in Postgres, with a fail-closed prefix-skew check against
the projection body), typed evidence boundaries hold (`chunks[]` never
masquerades as claims/facts), RRF fusion is deterministic and correctly
parameterized, and durable benchmark envelopes are preserved (the reader
prompt alone is compacted). The risks below are **operational**, ranked, and
should be watched on the next runs rather than treated as blockers.

## R1 — Nomination pollution under versioned stores (recall, not truth)

Chunk nomination in Lance has no current-version filter; superseded-version
rows can occupy candidate slots and then be dropped by Postgres
confirmation. Correctness is safe; effective recall thins as stores accrue
versions. LoCoMo stores are single-version, so the benchmark will not show
this — production stores will. Watch `dropped_by_hydration` on chunk paths;
if it grows with store age, P1 needs version hygiene (prune superseded rows
at projection time) or a current-only nomination filter.

## R2 — No post-drop refill

When hydration drops nominated candidates, recipes under-deliver versus
their declared `k` (no top-up fetch). Interacts multiplicatively with R1.
Cheap mitigation exists in recipe shape (wider `candidate_k`), which is why
`question_context` defaults to 200→50; watch whether confirmed counts sit
well below `k` in traces.

## R3 — Read-path store mutation on upgraded stores

Index bootstrap (`_ensure_*_index`) can create FTS/scalar indexes during
the **first read** of a pre-#193 store, and tail-optimize runs on writes.
Single-process this is safe and idempotent (checked via `list_indices`
with bounded jittered retries on Lance commit conflicts). Multi-replica
cold-start could stampede index creation; first reads on large upgraded
stores pay the build. Prefer a one-time warm (any write, or
`build_search_indexes`) when upgrading a big store.

## R4 — `question_context` cost profile is unmeasured at scale

One call = 2 embeddings + 4 index searches + 2 hydrations. Fine on smoke;
measure per-question latency/cost at publication tier before attributing
any remaining misses to retrieval quality. The query is also embedded twice
(claims-semantic and chunks-semantic channels each embed independently) —
an easy future dedup.

## R5 — The strongest D48 proofs live in the CI-only lane

The prefix-skew, non-current-version, and null-prefix drop behaviors are
asserted in live-Postgres tests that local fast-lane runs skip. Reviews
verified the SQL and code paths by reading; CI green on the full lane is
the actual proof. Empty-string `context_prefix` is an untested edge.

## R6 — Merged with author-acknowledged verification still running

The PR body notes final corrected-diff reviews and full pyright were still
running at merge ("posted as follow-up commits if needed"). Confirm those
follow-ups landed or explicitly close them out.

## Measurement plan

The single most informative check: re-score an **already-processed** store
under the v7 protocol (no re-ingestion — same memory, new retrieval) and
compare against its v5/v6-era result. The full run's miss taxonomy predicts
the Unknown bucket (60% of misses) and the zero-gold-session bucket (33%)
should both shrink; R4's cost shows up in the same run for free. Only
conversations whose live store (with its Lance index) survived can be
re-scored this way — Postgres-only forensic dumps lack the vector/FTS
projection and would need re-embedding first.
