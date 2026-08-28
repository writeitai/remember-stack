# Claude Opus review — D99 identity uncertainty and convergence

**Date:** 2026-08-28

**Reviewer:** Claude Code, `claude-opus-5`, effort `xhigh`

**Scope:** the D99 analysis, decision, binding identity/registry/schema/LoCoMo
design amendments, and sequencing contract. Claude was instructed not to edit.

## Round 1 — `REQUEST_CHANGES`

The reviewer agreed with the core tri-state/provisional/unlocked-provider
direction and found eight material specification gaps:

1. pre-D99 automatic exclusions had no machine-readable provenance or safe
   migration/revalidation/retirement path;
2. candidate-prefix completeness was incorrectly described as proof beyond the
   T1/T2 blocking recall ceiling;
3. the v16 `Unknown` guard was asserted in a banner but absent from the
   normative answer loop, cap interaction, and counters;
4. the fail-closed benchmark could emit proposals but could not converge
   unattended, which the protocol did not state;
5. equivalent cluster-review proposal identity and changed-membership
   supersession were undefined;
6. resolver in-call contention exhaustion was not reconciled with the outer
   work-ledger retry/DLQ contract;
7. the claim-normalize barrier was not explained for a cold reader; and
8. the content-before-`Unknown` policy was written like a library obligation
   even though only the repository benchmark harness can enforce it.

## Disposition

The corpus now:

- adds `supported_different | human | legacy_binary` exclusion basis,
  effectiveness, support pointers, and retirement provenance; migration keeps
  old automatic rows as ineffective audit;
- defines completeness as untruncated bounded work, never perfect blocking
  recall;
- puts the guard in the v16 loop with content-tool taxonomy, ordinary budget
  consumption, terminal-cap behavior, and `unknown_guard_retries`;
- pins `auto_merge_enabled=false`, forbids pre-score human acceptance, and
  separates proposal diagnostics from the ordinary score;
- keys proposals by deployment + sorted live roots + cluster-config
  fingerprint and replaces only overlapping pending proposals;
- separates resolver in-call retry from persisted worker backoff/DLQ;
- explains the D88/D90 claim-normalize barrier inline; and
- scopes mechanical answer behavior to the LoCoMo harness while the library
  only exposes typed ambiguity.

## Round 2 — `APPROVE`

Claude verified all eight findings as resolved and spot-checked the cited
resolver limits, tri-state model seam, test-only clusterer callers, and the
profile refresher's optimistic revalidation precedent.

Two non-blocking nits were also applied after approval: retirement now requires
`retired_by_decision_id`, and the design no longer claims the resolver itself
consults exclusions when only clustering currently does.
