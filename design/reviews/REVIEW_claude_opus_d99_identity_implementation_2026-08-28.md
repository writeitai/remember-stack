# Claude Opus implementation review — D99 identity uncertainty and convergence

**Date:** 2026-08-28

**Reviewer:** Claude Code, `claude-opus-5`, effort `xhigh`

**Scope:** complete staged implementation diff against `main`, including the
D99 migration, resolver, profile-triggered convergence, clustering review
deduplication, LoCoMo v16 guard, tests, and website documentation. Claude was
instructed not to edit files or build Docker images.

## Commands

Both rounds used the operator-required invocation:

```text
claude --dangerously-skip-permissions --model claude-opus-5 --effort xhigh -p "<review prompt>"
```

## Round 1 — `REQUEST_CHANGES`

Claude accepted the core tri-state, bounded-prefix, unlocked-provider, and v16
guard behavior but identified three merge-blocking areas:

1. deterministic proposal replay could supersede the only pending proposal and
   then conflict with its earlier `auto_resolved` row, leaving no replacement;
2. the migration omitted the binding legacy-inactive and effective-retirement
   constraints; and
3. migration classification, legacy exclusion inertness, convergence
   composition, changed-snapshot retry, and retry exhaustion lacked direct
   tests.

The implementation was amended to reopen only superseded automatic proposals
before retiring their overlapping predecessor; accepted and rejected history is
never rewritten. The missing database constraints were added. Focused tests now
cover migration upgrade/downgrade classification, legacy rows not blocking
clustering, proposal replay and member-set return, the profile-to-convergence
composition, resolver snapshot invalidation, and typed contention exhaustion.
Automatic cluster splits were also placed behind `auto_merge_enabled`, keeping
the production default genuinely fail-closed.

## Round 2 — `REQUEST_CHANGES`

Claude verified that all three round-one logic blockers were fixed. It then
found three mechanical schema pins still naming the preceding head:

- the migration lifecycle test expected `p9_19_0040`;
- the Compose release-gate workflow expected `p9_19_0040`; and
- the catalog contract did not include the five added checks and two new
  `NOT NULL` constraints.

Those pins now name `p9_20_0041`, `EXPECTED_CONSTRAINT_COUNTS` is updated, and
the test inventory includes the new pure convergence test. The review also
identified a possible review/cluster lock-order inversion. The cluster path no
longer row-locks review rows; supersession instead uses status-guarded updates
and requires the replacement to still be pending in the same statement.

## Follow-up findings retained

Non-blocking review notes remain for later, narrower work: deployment-wide
clustering serialization and pending-proposal scans, explicit convergence
failure telemetry, public presentation of provisional resolution confidence,
additional migration constraint rejection tests, and broader hard-forget
coverage for non-content provenance fields. The accepted design deliberately
keeps automatic merging disabled until calibration and deliberately allows
ordinary work-ledger retry/DLQ behavior after bounded resolver contention.

## Validation after review

- `ruff format --check` and `ruff check`: passed
- Pyright over the changed implementation and tests: 0 errors
- unit inventory: 77 unit paths, 50 integration paths, 127 discovered
- unit suite: 1,056 passed, 6 skipped
- focused LoCoMo/spine suite: 97 passed; PostgreSQL-only cases skipped locally
- website TypeScript check: passed

The PostgreSQL migration and integration cases remain CI-authoritative because
the local PostgreSQL/Colima data directory was unavailable. No local Docker
build was attempted during either review round.
