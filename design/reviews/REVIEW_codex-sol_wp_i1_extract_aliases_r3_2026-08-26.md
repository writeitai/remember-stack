# PR 308 — WP-I.1 extract aliases — round 3

## Verdict: Approve

No P0 or P1 findings. All prior P1s are closed.

## Prior P1 closure audit

- **Round-2 P1 — fallback canonical written as ungrounded `provenance=source`: Closed.** `src/rememberstack/spine/resolver.py:524-565` now independently checks the emitted surface and the canonical name against `claim.claim_text`. The value selected as `source_text` is therefore itself claim-grounded; when neither candidate appears, `source_text` is `None`, the canonical `llm_canonical` alias is retained, and the `source` UPSERT is skipped.
- **Round-1 P1 #1 — source provenance requires claim evidence: Closed.** The complete resolver path now enforces the provenance invariant rather than relying on the normalizer prompt or caller correctness. `surface_appears_in_claim` remains word-bounded and case-insensitive, so `App` is accepted as a claim span but not as a substring of `Application`.
- **Round-1 P1 #2 — acceptance tests must exercise shipped E3/resolver: Closed.** The production-composition tests remain at `src/tests/spine/test_resolver.py:534-623`, covering bare `game`, mintable `FIFA 23`, and grounded `source/App` plus `llm_canonical/Application`. The new negative assertion at `src/tests/spine/test_resolver.py:495-531` uses a claim containing neither spelling and requires that no `source` alias exist, while preserving `llm_canonical/Application`.

The round-3 change is correctly scoped: it does not alter the WP-I.1 bare-noun gate, alias idempotency, generic-identifier guard writer, current exact-T0 behavior, or the deferred type cut.

## Verification

- Reviewed `origin/main...origin/feat/wp-i1-extract-aliases` at `fde1bd6040e3ab6fe33b7970ddbbdf93fece1cba` against main `ed7bff50ead308d25146d72138691db187d55f88`.
- CI unit inventory: `1012 passed, 5 skipped`.
- Focused eligibility/E3 tests: `8 passed`.
- Resolver integration collection: `11 skipped` because `REMEMBERSTACK_DATABASE_URL` is unset and no test PostgreSQL instance is running; the database assertions were reviewed statically.
- Changed-file Ruff, Ruff format, Pyright, and `git diff --check`: clean.
- No applicable WP-I.1 eval-banana check is present in the repository's discovered `eval_checks` material.
- Tracked worktree remained unchanged.
