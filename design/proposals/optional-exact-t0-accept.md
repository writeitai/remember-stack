# Proposal: optional exact-lemma T0 auto-accept (default off)

**Status:** open, unchosen — **not binding**, not implemented.
**Date:** 2026-08-26
**Binding baseline:** D95; T0 is a candidate list, never a merge, in
[`entity_identity_and_retrieval_design.md`](../../plan/designs/entity_identity_and_retrieval_design.md)
§3.1.
**Analysis:**
[`entity_identity_and_retrieval_analysis.md`](../../plan/analysis/entity_identity_and_retrieval_analysis.md)
§5.1.
**Sequencing:** do **not** ship this flag in WP-I.5
([`entity_identity_and_retrieval.md`](../../plan/plans/entity_identity_and_retrieval.md)).

## Problem

Shipped T0 (pre-D95) treated an exact match on `aliases.normalized_lemma`
as a verdict at confidence 1.0. That made father/son impossible: the
second person never reached a profile or a judge.

D95 removed that verdict. Repeats of a **known** person are supposed to
be cheap via **T3** (mention+claim embedding vs that entity’s profile),
not via resurrecting T0. Empty profile, conflicting profile, or several
exact candidates go to **T4**.

Operators who remember the old exact-hit asked whether that path could
**remain in the code**, turned **off by default**, and be switched on
manually only after a memory already has a large entity table.

The cost T0 would save versus T3 is one embedding lookup. The cost of
one false merge is every later observation, relation, hop, and forget
attached to the wrong id.

## Proposed change (if ever adopted)

A per-deployment flag on `resolver_versions.tier_config`, for example
`t0_exact_accept`, default **`false`**.

| Flag | T0 with exactly one distinct active `entity_id` | T0 with 0 or many |
|---|---|---|
| `false` (D95, ship this) | candidate list; T3 or T4 decides | mint, or T3/T4 among many |
| `true` (this proposal) | auto-accept at confidence 1.0 (pre-D95) | same as D95 |

No common-name census. No auto-flip at an entity-count threshold. The
operator who sets the flag is asserting that **same cleaned spelling
means same referent** in *this* store.

## Adoption trigger — not corpus size

**Do not adopt because the entity table is large.** A large store has
**more** name collisions (birthday paradox), not fewer. Case A (lemma
currently unique) becoming Case B (two referents, one spelling) is more
likely as the table grows. The first collision of a previously unique
name is the moment auto-accept is fatal, and it is silent.

The intuition “after lots of entities exist, new mentions are repeats”
describes **T3+profile**, which D95 already uses as the scale path.
Empty-profile cold start (second `Jan`) is not solved by having a large
table; it is made worse.

Adopt this proposal only if **all** of the following become true:

1. The tenant’s names are an operator-asserted **closed unique
   namespace** — SKU codes, employee numbers, inventory ids — not
   person given names, not vendor shorthand that can also be a product
   (`SAP`), not any string two humans can share.
2. The D22 golden-pair harness for that tenant includes same-lemma
   **non**-matches and still passes with the flag on.
3. Unmerge cost and D24 blast-radius are accepted as the recovery path
   for the first silent glue. Hub merges never auto-accept remains.

A **better** cheap unique-key path, if one is needed later, is
**identifier T0**: exact match on strings minted to be unique (email,
LEI, ORCID, ISBN). That is not name-lemma T0 and is not this proposal.
D20 already treats external authority as an accelerator that most
entities miss.

## Rejected automatic policy

The library will **not**:

- enable `t0_exact_accept` when `entity_count` crosses N;
- auto-accept “distinctive” lemmas and escalate “common” ones via a
  given-name stoplist (thousands of locale-dependent names; the second
  `Jan` still glues);
- ship the flag in WP-I.5 “just in case.” An unused production switch
  will be flipped under T4 cost pressure.

## Costs and cautions

- Silent homonym glue (father/son, two employees) whenever the flag is
  on and the lemma is shared.
- Eval can hide the damage if `judge_pair` still treats lemma equality
  as a match — WP-I.3 must land first regardless.
- Unmerge is possible (D24) but is not a substitute for not gluing.
- Identifier-shaped T0, if ever designed, must not reuse this flag:
  mixing “this SKU is unique” with “this given name is unique” is how
  the footgun returns.

## Non-goals

- Changing D95’s default.
- Replacing T3+profile as the cheap repeat path.
- A world given-name census.
- WP-I.5 implementing the switch.

## Alternatives (why this is not the baseline)

| Alternative | Why it is not the default |
|---|---|
| T0 exact as always-verdict | Homonyms impossible (D95) |
| Distinctive lemma + common-name list | Unscalable census; second `Jan` still glues |
| Enable exact-T0 at large `entity_count` | Backwards: collisions peak at scale |
| T3+profile for repeats | **Chosen** scale path |
| Identifier T0 (LEI/email/ORCID) | Honest unique-key accept; separate design if needed |
