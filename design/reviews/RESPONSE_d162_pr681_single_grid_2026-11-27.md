# Response to PR #681 reviews, rounds 1–4

The owner limited D162 to one grid per version with the same chunker
version, legacy ID allocation and existing work keys. READY versions
keep their grids. Recovery deletes the entire unpublished failed grid
and restarts at CHUNK. [Revised design](../../plan/designs/split_oversized_chunks_design.md).

- **Round 1 (`988b71bd1`).** The delivery collision and E2 lookup objections concerned
  coexisting grids, which D162 removes. The pre-D140 concern assumed
  rehashing surviving rows. Fresh CHUNK applies the ordinary D140 date
  lookup and fallback. The legacy `context_prefix` wording is outside D162.
- **Round 2 (`8b28dd4d7`).** The old claims and facts tails cannot absorb new work
  from this replay because eligibility requires that neither tail has
  started. The CHUNK enqueue concern still applies. The recovery
  transaction reschedules the succeeded CHUNK row and clears the
  stored cuts. CHUNK completion reopens the matched embed attempt's row.
  The schema wording about packing parameters is outside this change.
- **Round 3 (`446f7f2cf`).** Fact admission fencing and restoration of testimony
  retired by repacking are moot. Recovery requires an unpublished
  version with no downstream work started or evidence. Legacy empty
  sources-only completions keep their original path. D80 compatibility
  column comments are outside this change.
- **Round 4 (`b51e25353`).** The proposed flush key changes, displaced testimony
  handling and reuse from a completed same-version grid are moot.
  Deleting the full grid also removes the stale D80 metadata case on a
  surviving row. Fresh embedding stamps use the resulting chunk count.

The embed-to-claims barrier establishes downstream ordering. E0 and P1
can expose source results earlier, so publication remains an independent
transactional check. The PostgreSQL 18 probe observed both conditions in
the same failed version. [Source audit](../../plan/analysis/split_oversized_chunks_analysis.md#why-replay-is-limited-to-unpublished-versions).

The implementation must qualify the provider routes and run the frozen
benchmark equality check. [Acceptance](../../plan/designs/split_oversized_chunks_design.md#5-acceptance).
