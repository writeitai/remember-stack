# PR 308 — WP-I.1 extract aliases — round 2

## Verdict: Request changes

Round-1 P1 #2 is closed: the App/FIFA coverage now supplies claim text containing the relevant surfaces and `test_e3_mints_fifa_23_and_app_application_on_shipped_resolver` drives `NormalizeRelationsHandler` through the production `CascadeResolver`. Round-1 P1 #1 remains open because the fallback path still writes an ungrounded value with `provenance=source`.

## P0 findings

None.

## P1 findings

### 1. The fallback canonical name is still written as `source` without being grounded in the claim

`src/rememberstack/spine/resolver.py:524-558` validates only `reference.mention_surface()`. When that value is absent from the claim, line 528 substitutes `reference.name`; lines 552-558 then unconditionally upsert the substituted name with `provenance="source"` without checking whether that name occurs in the claim.

The new negative test makes the remaining defect explicit. `src/tests/spine/test_resolver.py:501-529` resolves `EntityRef(name="Application", surface="App", ...)` against `_claim()`'s Karel-Dvorzak/Atlas text, which contains neither `App` nor `Application`, and then requires `("source", "Application")` to exist. Although the hallucinated `App` spelling is no longer stored, an equally ungrounded canonicalization is still represented as source-observed. This violates design §4.4's requirement to write "the surface form actually seen in the claim" as `source`. It also has current identity impact because T0 searches all alias provenances, so the false source row participates in exact resolution.

Only upsert a `source` alias after validating the exact value to be stored. If the emitted surface is ungrounded, independently test the canonical name and use it as `source` only when it appears in the claim; if neither value is grounded, retain the `llm_canonical` alias but write no `source` alias. Change the negative test to assert that no source alias is present for this claim.

## Round-1 P1 closure audit

- **P1 #1 — source provenance requires claim evidence: Not closed.** The word-bounded helper correctly rejects `App` against unrelated text and against `Application`, but `_record` does not validate its fallback before assigning source provenance.
- **P1 #2 — tests must exercise shipped E3/resolver and grounded App text: Closed.** `src/tests/spine/test_resolver.py:533-622` composes the production handler and resolver, proves bare `game` does not reach the alias table, permits `FIFA 23`, and records `source/App` plus `llm_canonical/Application` from a claim containing `App`. The direct resolver replay test also covers alias upsert idempotency on one entity id.

No other P0/P1 findings.

## Verification

- Reviewed `origin/main...origin/feat/wp-i1-extract-aliases` at `5d80db3a2bfb83e37cb1975c88604ab3f0b03790` against main `ed7bff50ead308d25146d72138691db187d55f88`.
- Unit inventory: `1012 passed, 5 skipped`.
- Focused eligibility/E3/resolver run: `8 passed, 11 skipped`; the 11 PostgreSQL-backed resolver tests were skipped because `REMEMBERSTACK_DATABASE_URL` was unset, so their database assertions were reviewed statically rather than counted as executed evidence.
- Changed-file Ruff and Pyright: clean. `git diff --check`: clean.
- Tracked worktree remained unchanged.
