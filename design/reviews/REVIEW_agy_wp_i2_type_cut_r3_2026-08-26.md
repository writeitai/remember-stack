# Adversarial implementation review (round 3): PR 311, WP-I.2 hard type cut

**Reviewer:** Antigravity (`agy`)  
**Date:** 2026-08-26  
**PR:** `writeitai/remember-stack#311`  
**Branch:** `origin/feat/wp-i2-type-cut` vs `origin/main`  
**HEAD reviewed:** `1e8510230799bc47e350d57f987aeb49d64e4160` (verified)  
**Verdict:** **Approve**

## Findings

No P0 or P1 finding remained in this review. The reviewer verified that the
implementation met the WP-I.2 runtime acceptance contract and closed the prior
blocking findings.

Two non-blocking observations were recorded:

1. `EntityIndexPort.search_entities_scored` still accepted an ignored
   `entity_type` parameter after the implementation stopped filtering on it.
2. `test_e3_unknown_entity_type_gate.py` remained as a skipped D86 placeholder
   even though active D96 proofs existed elsewhere.

Both were removed in the follow-up to the independent Claude r3 review.

## Prior-finding closure

| Prior finding | Result on reviewed HEAD |
|---|---|
| Mention helper and graph views still depended on dropped columns | Closed: all dependent views are replaced before `DROP COLUMN`. |
| `predicate_signatures` remained on bootstrap, catalog, pack, or E3 live paths | Closed: no live reader or writer remained. |
| `GraphNode.type` and P2 Cypher still expected type | Closed: graph projections and row decoding are untyped. |
| Downgrade lost the entity-provenance `EXISTS` condition | Closed: the complete condition was restored. |
| Resolve and graph hydration still constructed typed models | Closed: HTTP, SDK, engine, envelope, and graph consumers are name/id based. |
| Catalog counts, deleted objects, public filter allowlists, and forget SQL were stale | Closed: catalog and forget contracts match the head schema. |
| P3 paths and links remained type-shaped | Closed: `entities/<entity_id>/` and `../../documents/` are tested. |
| LoCoMo generation and manifest pins were stale | Closed on the reviewed HEAD. |
| Global literal rewrite damaged unrelated INSERT tuples | Closed by `2b87db44`; targeted and full suites found no failure. |

## Checks reported

- `uv run pyright src/rememberstack benchmarks` — 0 errors.
- `uv run ruff check src/rememberstack benchmarks src/tests website` — passed.
- Targeted D96 suite — 95 passed, 79 skipped, 0 failed.
- Broad local suite — 768 passed, 629 skipped; database-only fixture errors
  were attributed to the absent local database URL.
- GitHub Actions for the reviewed SHA — all required CI lanes green, including
  workers, surfaces, adapters, contract smoke, quality, unit, docs build, CLA,
  and PR gate.

## Verdict

**Approve.**
