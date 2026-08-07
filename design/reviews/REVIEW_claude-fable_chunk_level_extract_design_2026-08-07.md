# Review — D84 chunk-level extract design

- **Reviewer:** claude-fable (session; CLI pass interrupted — completed offline
  against the same corpus as Codex)
- **Date:** 2026-08-07
- **Scope:** `plan/analysis/chunk_level_extract_analysis.md`,
  `plan/designs/chunk_level_extract_design.md`, D84; cross-read `e2.py`,
  `e1.py` follow-ups, `processing.py`, `work_ledger.py` unique key,
  `readiness.py` version-only filter

## Verdict

### **Accept with changes**

Chunk grain is the right decision. `ProcessingTarget.CHUNK` and D56 already
point there. I agree with Codex that the **barrier and readiness contracts**
must be tightened before implementation is “done,” and that mixed-image
rollouts need an explicit monorepo same-image rule.

## Findings

| ID | Sev | Title |
| --- | --- | --- |
| F-1 | P1 | Barrier must complete inside the ledger success transaction (same as Codex P1.1) |
| F-2 | P1 | Readiness / connector finalize currently ignore chunk-targeted extract (Codex P1.2) |
| F-3 | P1 | Mixed-image: old E2 serial path can claim new chunk rows — require same-image worker roll (Codex P1.3) |
| F-4 | P2 | Fan-out of 1e5 chunk rows should be batched; document memory bound |
| F-5 | P2 | Neighbour bundle still loads all chunks per job — acceptable for 1M; watch 10M |

## Recommendation

Proceed to implementation **only with** atomic `complete_chunk_extract`, readiness
+ lifecycle updates, and deploy note. Do not ship fan-out alone.

Design doc amended 2026-08-07 to incorporate F-1–F-3.
