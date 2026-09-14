# D122/D123 implementation checkpoint — 2026-09-14

Implementation PR: [#403](https://github.com/writeitai/remember-stack/pull/403).
Wip checkpoint `a01217c07b097b8887cf4c9465d78065d2f9bacd`.
Antigravity read-only review SHA: `f60fb511623fdafbef78582dcbf08db7d7863c6a`
(`agy --dangerously-skip-permissions --print-timeout 180m0s`).
Verdict: **acceptable** for parent integration review of the D123-independent
slice. Blockers 0, material findings 0. Two optional nits (hardcoded `> 4`
vs `MAX_CONTEXT_REFS`; alias `strip` compared to unstripped card name) were
left unfixed so the reviewed code SHA stays the contract head.
Binding designs: [D122](../designs/document_reference_context_design.md),
[D123](../designs/contextual_fact_nomination_design.md).
Parent owns final integration, review, merge, and release. This branch must
not be self-merged.

## What this head implements

D123's independent path is implemented on this head:

- The existing normalizer may emit up to four extra `EntityRef` context
  references per assertion. Overflow stays on the frozen response and does not
  reject the assertion or prove novelty.
- Original emitted ordinals are kept. Equal names stay distinct until ordinary
  entity resolution assigns ids. Aliases that resolve to one entity keep the
  first ordinal; staging does not abort the assertion.
- Canonical subject and object are excluded after resolution, not by casefolded
  names.
- Bindings are written in the same transaction as the application and version
  staging rows. An application with zero junction rows is a complete empty set.
  There is no `context_complete` flag and no legacy conversion.
- Staging validates a live resolver decision for this claim and entity.
- Nomination keeps the existing same-subject baseline of 20 and may add up to
  eight additional facts whose supporting applications share incoming context.
  Empty context is not a new-fact authorization. Extra facts cannot displace a
  baseline target.
- Snapshot fingerprints include junction rows, reverse-closure membership, and
  supporting applications that affect extra nomination even when they sit
  outside the bounded assertion payload. Empty UUID arrays are typed
  `uuid[]`, not sentinel values.
- Hard-forget deletes `selection_results` and cascaded context bindings.

D122 helpers exist for later E2 integration and are tested without a live
Selection worker:

- Engine-supplied passage labels, not quote search. A missing or forged label
  rejects the whole card.
- Distinct emitted cards are preserved. One passage may introduce two
  referents. Cards are capped whole.
- Claimify render labels are request-unique (`R1`, `R2`, …). Passage support
  keeps the supplied catalog labels. The 4096-character bound measures the
  actual rendered block.
- Claimify reuse hashing includes empty preceding Selection producers.

## What this head does not implement

E2 Selection freeze, the representation barrier, Claimify scheduling, and
occurrence remapping are intentionally absent. They depend on completed
multi-span extraction ([#400](https://github.com/writeitai/remember-stack/pull/400))
and then D120/D121. This branch does not invent a parallel multi-span
implementation.

`PassageSupport` / `GroundedPassage` are pre-integration stand-ins. When D119
lands, replace them with its catalog types and mapping helpers, keep
request-unique card labels, and keep source labels usable by Claimify
grounding.

The migration still follows `p9_30_0051`. Rebase onto the D119 revision and
keep populated-store refusal before final migration validation.

## Why there is no Claimify receipt table

D122 requires one source-owned Selection-result store. That is
`selection_results`: the frozen Selection response, cards, generation, and
stable input hash. Claimify's extra reuse basis is the ordered Selection-input
hashes of the previous eight source chunks plus the reference-policy
generation. Existing chunk completion and extraction reuse already persist a
content/input hash per occurrence. When E2 is wired, that existing receipt can
carry `claimify_input_hash`; a second Claimify table would duplicate the
per-chunk completion/reuse contract. If integration proves the existing row
cannot hold that key, document the extra store then. Do not add it
speculatively.

## Tests run on this head

Dedicated database only:
`REMEMBERSTACK_DATABASE_URL=postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55442/ugm_d122_test`.

- Unit: `test_selection_references`, `test_context_references`,
  `test_e3_context_references`, `test_e3_bare_head_noun`,
  `test_e3_claim_normalize_fanout`, `test_locomo_protocol` — 85 passed.
- PostgreSQL: `test_d123_context_nomination` (8), writer acceptance, and
  migration graph / populated-store refusal / fresh lifecycle catalog
  contract — 37 passed after catalog-count and merge-status corrections.
- Pyright on changed library files: 0 errors.
- Ruff check on changed files: passed.

No paid LoCoMo run, no remote benchmark access, no provider-default change.
Rendered-card character accounting is proven with the actual formatted block in
unit tests, not billed model tokens. Extra-nomination candidate counts are
proven with the writer fixture (baseline 20 preserved, union ≤ 28), not as a
semantic-quality claim.

## Remaining risks

- E2 worker-order, barrier restart, 500-version claim reuse, and forget during
  Selection/Claimify are untested because those workers are not on this branch.
- Context resolution cost is not measured against a live resolver model tier.
- Constraint counts and table inventory must be re-checked after the D119
  migration is inserted.
- Parent review of #403 is still required before merge.
