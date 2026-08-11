# Round-4 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (gpt-5.6-sol)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `c3818255`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Prior review:** `design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r3b_2026-08-11.md`

## Summary

Commit `c3818255` resolves the narrow prior B1 and B2 defects. The extract
generation now flows from the extract barrier into fan-out, claim payloads,
`ClaimNormalizeBarrier`, claim barrier counts, the observation-flush payload,
and derived readiness; all three claim-set SQL statements filter
`claims.extractor_version` (`src/rememberstack/spine/work_ledger.py:287-307,
346-373,621-737,740-771,1148-1186`,
`src/rememberstack/spine/readiness.py:105-120,285-331`,
`src/rememberstack/workers/base.py:59-73`). A later extractor generation no
longer enlarges the old generation's expected set. **Prior r3b B1 is resolved.**

Newly grounded claims now take `asserted_at` from source time and persist it
through the claim insert (`src/rememberstack/workers/e2.py:578-602`,
`src/rememberstack/model/claims.py:179-203`,
`src/rememberstack/spine/claim_catalog.py:266-284`). Supersession loads that
time and orients each pair before the prompt/write path, with a stable relation
ID tie-break for equal or absent timestamps
(`src/rememberstack/spine/supersession.py:163-201,381-460,501-516`). This removes
the specific processing-order direction error from r3b B2. **Prior r3b B2 is
resolved.**

Prior B3 is not resolved in the ordinary observation path. The new reverse
branch depends on the open candidate's `valid_from`, but the normal first/new
observation paths do not store the incoming claim's `asserted_at` there. A 2024
observation arriving first therefore still has `valid_from = NULL`; a 2019
superseding assertion arriving second falls through to the forward branch and
caps the 2024 row at 2019. The new test exercises only the standalone timestamp
comparator, not this production data flow. The implementation also still lacks
the binding deployment/version coordinate validation called for by design
sections 5.1-5.3.

Therefore this is **not** the simplest solid mergeable v1 yet.

## Blocking findings

### B1 — The reverse-arrival observation path has no source time for ordinary open candidates

`add_observations` correctly loads each incoming claim's `asserted_at` and passes
it into `_add_with_block`
(`src/rememberstack/spine/observation_adjudication.py:139-174`). On the first
observation for an entity, however, `_insert_new` is called without
`valid_from=asserted_at`, and the in-transaction candidate is likewise remembered
without it (`src/rememberstack/spine/observation_adjudication.py:208-228`). The
same omission exists on the no-open-candidate and clear-novelty paths
(`src/rememberstack/spine/observation_adjudication.py:229-282`). `_insert_new`
defaults the field to `None` and stores that value
(`src/rememberstack/spine/observation_adjudication.py:631-665`).

The next version's flush loads `observations.valid_from` as the candidate source
time (`src/rememberstack/spine/observation_adjudication.py:797-806`). When the
2024 assertion arrived through one of the ordinary paths above, that value is
`NULL`. `_is_strictly_earlier(2019, NULL)` is explicitly false
(`src/rememberstack/spine/observation_adjudication.py:781-792`), so the reverse
branch at lines 407-461 is bypassed. The forward branch then caps the existing
2024 observation at the incoming 2019 boundary and inserts 2019 as the open
successor (`src/rememberstack/spine/observation_adjudication.py:462-505`)—the
same wrong result identified in r3b B3.

Even when a candidate happens to have `valid_from`, the semantic verdict is
obtained with arrival-oriented `existing`/`new` prompt roles before source-time
orientation is checked
(`src/rememberstack/spine/observation_adjudication.py:321-327,407-412`). Forward
and reverse arrival can therefore ask the model different questions before the
write-only correction runs.

The added test calls `_is_strictly_earlier` directly
(`src/tests/workers/test_e3_claim_normalize_fanout.py:155-167`); it never inserts
the newer observation first or verifies stored windows after the older version
arrives. Preserve or derive source time for every observation that can later be
a candidate, orient the comparison and window write by it, and cover the same
two assertions in both version-completion orders end to end.

### B2 — Claim fan-out and handler coordinates are still not deployment/version bound

The binding expected-set contract requires a deployment-scoped
claim -> chunk -> representation -> version membership check, and the handler
must validate claim, representation, version, document, and deployment before
work (`plan/designs/e3_claim_level_normalize_fanout_design.md:84-95,120-145`).
The fan-out and barrier SQL now pin `extractor_version`, but still constrain the
claim set only by `representation_id`, `chunker_version`, and that extractor pin;
they do not bind the supplied deployment or version
(`src/rememberstack/spine/work_ledger.py:1148-1186`).

The handler loads the target claim globally by `claim_id`, parses the payload
version but never compares it, ignores the payload's own `claim_id`, checks only
`doc_id` plus membership in the supplied representation's chunk-ID set, and
checks only that `extractor_version` is present—not that it matches the claim
(`src/rememberstack/workers/e3.py:147-182`,
`src/rememberstack/spine/claim_catalog.py:141-153,335-340`). The chunk rows
already expose `version_id`, but the handler discards it when building the set
(`src/rememberstack/model/chunks.py:97-113`,
`src/rememberstack/workers/e3.py:173-181`). It also never compares the claim or
representation to `work.deployment_id`, which is then used for relation and
staging writes (`src/rememberstack/workers/e3.py:182-229,457-465`).

A malformed/replayed row can consequently pair tenant A's claim and
representation with tenant B's processing deployment, or lie about `version_id`
while still passing the handler. That is the cross-tenant/version payload case
the binding design explicitly requires rejecting. Use one lineage-scoped lookup
or equivalent checks that bind every coordinate, including the extractor pin,
before model, resolver, fact, or staging work; cover wrong target/payload claim,
version, representation, deployment, and extractor generation.

## Non-blocking coverage notes

- The extract-generation tests assert the presence of a predicate in SQL strings
  (`src/tests/workers/test_e3_claim_normalize_fanout.py:48-67`). A database test
  with two extractor generations would more directly protect the closed-set
  behavior, but the traced pin is sufficient to resolve the prior defect.
- The supersession test proves the orientation helper's ordering only
  (`src/tests/workers/test_e3_claim_normalize_fanout.py:129-152`). An end-to-end
  reverse-version supersession test remains desirable to protect the actual
  closure and transcript path.
- The prior hard-forget fixture coverage note remains: the scoped scrub is
  credible, but a staged-row/control-row test would better protect plaintext
  erasure.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **27 passed in 3.85s**.

Supplementary database-focused command:

```text
uv run pytest src/tests/spine/test_observation_adjudication.py \
  src/tests/spine/test_supersession.py -q
```

Result: **17 skipped in 0.87s** in this environment, so it supplied no runtime
coverage of reverse-order windows.

## Mergeability

**No.** The extractor-generation set pin and relation source-time orientation
are now in place, and the previously solid D88 lock/atomic handoff/barrier chain
remains intact. But ordinary observations still lose the very candidate source
time the reverse-arrival branch needs, so cross-version completion order can
still reverse the windows. The explicitly binding cross-deployment/version
coordinate check is also still absent. Both are correctness requirements, not
v2 hardening or optional architecture.
