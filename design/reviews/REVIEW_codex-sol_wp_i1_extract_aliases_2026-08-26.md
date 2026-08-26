# PR 308 — WP-I.1 extract aliases review

## Verdict: Request changes

The scope boundaries are mostly correct: `EntityRef.surface` is optional while `type` remains, `Application` is not in the bare-noun refusal set, exact T0 still auto-accepts rather than becoming the I.5 candidate-list algorithm, there is no exact-T0 production flag, and no type/schema cut leaked in from I.2. The alias UPSERT also targets the existing `(deployment_id, entity_id, normalized_lemma, provenance)` uniqueness contract and counts distinct ids for the guard.

Two P1 issues remain.

## P1 findings

### 1. `provenance=source` is accepted without proving that the surface came from the claim

`src/rememberstack/spine/resolver.py:523-553` takes `reference.mention_surface()` and unconditionally stores it in `mentions.surface_form` and an alias with `provenance="source"`. The resolver never compares that value with `claim.claim_text`, and E3 performs no such grounding check before calling it.

That breaks the binding meaning of the new field: `surface` is the span as it appeared in the claim, and a `source` alias claims precisely that provenance. A malformed or hallucinated normalizer response can therefore attach any spelling to an entity. This has immediate identity impact in the current release because `_T0_EXACT` searches all alias rows: a later occurrence of that invented spelling can auto-merge to the poisoned id at confidence 1.0.

The new tests accidentally demonstrate the gap. `src/tests/workers/test_e3_bare_head_noun.py:116-147` supplies `surface="App"` while its imported `_claim()` contains “The caching process stores hot keys.” Likewise, `src/tests/spine/test_resolver.py:367-423` writes `App` against a claim about Karel Dvorzak and Atlas. Neither test uses claim text containing `App`, despite the acceptance criterion explicitly requiring claim text `App` to produce source alias `App` and canonical alias `Application`.

Require the source surface to be grounded in the claim before assigning `source` provenance (or carry and validate an actual span). Cover both the positive `App` claim and a response whose claimed surface is absent from the claim; the latter must not become a source alias.

### 2. The bare-noun acceptance tests stop at a recording fake instead of the shipped E3 → resolver path

`src/tests/workers/test_e3_bare_head_noun.py:21-83` does execute the production `_normalize_claim` gate, which is useful, but its resolver is `RecordingResolver`. Consequently, `test_normalize_resolves_fifa_23` proves only that a spy was called and returned its synthetic `created=True`; it does not prove that shipped `CascadeResolver` minted `FIFA 23`. The `game` case similarly proves only that the spy was not called, without checking the entity table. The real resolver test at `src/tests/spine/test_resolver.py:367-428` bypasses E3 entirely and covers only the separately constructed `Application` reference.

This does not meet the requested acceptance-proof condition that tests drive shipped E3/resolver rather than a substitute at the resolution boundary. Add a PostgreSQL-backed test using the production handler/resolver composition (the existing E3 integration harness is the natural home) and assert all three externally visible outcomes: no entity for bare `game`, an entity may be minted for `FIFA 23`, and a claim that actually contains `App` produces exactly the `source/App` and `llm_canonical/Application` rows on one id. Replay the same processing input or claim coordinate and assert the alias rows remain one per provenance.

## Acceptance and scope audit

| Check | Result | Evidence |
|---|---|---|
| `game` refused; `FIFA 23` eligible | Partial | Production E3 gate is exercised, but only with `RecordingResolver`; no shipped mint/table proof. |
| Claim text `App` → source `App` + canonical `Application` | Fails as an acceptance proof | Alias SQL is present, but both new tests use claims that do not contain `App`, and production accepts ungrounded source surfaces. |
| `EntityRef.surface` optional; types remain until I.2 | Pass | `surface: str | None = None`; `type` and typed schema/writes remain. |
| Bare-noun list preserves canonical `Application` | Pass | `Application` is explicitly covered as eligible; filtering is performed on canonical `name`, so `surface="App"` does not drop it. |
| Alias upsert idempotent on mint/T0 replay; two provenances on one id | Code path is correct, proof incomplete | UPSERT conflict target matches the schema and direct resolver test covers sequential T0 replay, but the PostgreSQL proof was unavailable locally and does not run through E3 or a source-grounded claim. |
| Guard writer exists and counts distinct ids | Pass | `_UPSERT_GENERIC_GUARD` uses `COUNT(DISTINCT entity_id)` and is called from `_record`. It does not alter T0. |
| No T0-as-candidates change (I.5) | Pass | `_T0_EXACT ... LIMIT 1` and immediate T0 `_record` return remain. |
| No type-column/type-contract drop (I.2) | Pass | No migration is included; entity and mention type fields/writes remain. |
| No exact-T0 production flag | Pass | None added. |
| Dead WP-I.1 production code | Pass | The eligibility predicate, surface helper, alias UPSERT, and guard writer all have shipped callers. |

## Verification

- Reviewed `origin/main...origin/feat/wp-i1-extract-aliases` at `b28106e58c1bcd607f3fdf2f2e07979fa337d87e` using Git refs, independent of the mutable shared worktree.
- Stable archived PR unit pack: `1011 passed, 5 skipped`.
- Focused E3/eligibility plus resolver collection: `7 passed, 8 skipped`; all eight resolver integration tests skipped because `REMEMBERSTACK_DATABASE_URL` was unset, so I do not count the PostgreSQL alias assertions as locally executed evidence.
- Changed-file Ruff, Pyright, Ruff format, test-inventory, and `git diff --check`: clean.
- No applicable WP-I.1 eval-banana check is present in the repository’s discovered `eval_checks` material.

## Nits

None.
