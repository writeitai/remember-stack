# Review: optional exact-lemma T0 proposal

- **Reviewer:** Codex gpt-5.6-sol
- **Date:** 2026-08-26
- **PR:** 307
- **Verdict:** Approve

## Direct answers

1. **Is T0-never-auto-merge still the binding default?**

   **Yes.** D95 still says that T0 only lists distinct active candidate ids and that T3 or T4 supplies the verdict (`decisions.md:3871-3894`). The binding design repeats “T0 never auto-accepts” in §3.1 and retains the father/son acceptance case. The new document identifies itself as “open, unchosen,” “not binding,” and “not implemented” (`design/proposals/optional-exact-t0-accept.md:3-12`). The sequencing plan makes exact-lemma auto-merge a WP-I.5 non-goal while WP-I.5 itself still implements candidate-only T0 (`plan/plans/entity_identity_and_retrieval.md:74,91-97`). There is no competing binding instruction.

2. **Is enabling exact-lemma T0 because the corpus is large correctly rejected?**

   **Yes.** The rejection is both explicit and correctly reasoned. More entities create more opportunities for two referents to share a cleaned spelling; corpus size cannot establish uniqueness. Known repeats already have the intended cheap path—mention-plus-claim versus profile at T3—while exact T0 would silently glue the first collision before profile or T4 could contribute. Analysis §5.1 explains both the currently-unique-name case and the already-colliding-name case, rather than relying only on the “birthday paradox” label. D95, design §3.1.1, the proposal, and the plan all reject entity count as an enablement trigger.

3. **Is the unchosen proposal's adoption trigger honest and not a back door into WP-I.5?**

   **Yes, for this PR.** The proposed exception is scoped to a deployment whose complete naming domain is operator-asserted to be a closed unique namespace, where “same cleaned spelling means same referent”; it expressly excludes person names, vendor shorthand, a common-name stoplist, and any count-based auto-flip (`design/proposals/optional-exact-t0-accept.md:43-69,77-86`). It also keeps hub/blast-radius protection and acknowledges unmerge cost. Most importantly, nothing authorizes implementation: the proposal is unchosen, its non-goals exclude WP-I.5, D95 calls it unchosen, and the plan explicitly excludes the flag from WP-I.5. Adopting it would require a later binding design/decision change; this PR does not pre-authorize that change.

4. **Cold-reader gaps in analysis §5.1, D95, or the proposal.**

   There is no gap that makes the binding design false. Analysis §5.1 is self-contained about the Case A/Case B failure, why scale is not proof of uniqueness, and why T3 is the scale path. D95 clearly distinguishes the binding rule from the unchosen proposal.

   The proposal has two non-blocking ambiguities worth resolving before anyone considers adopting it:

   - Its second adoption condition says a tenant-specific D22 harness must contain same-lemma non-matches and pass with the flag on (`optional-exact-t0-accept.md:66-67`). Under the table at lines 38-41, a same-lemma negative presented to T0 with one existing id deterministically auto-accepts, so that condition appears impossible unless those negatives are explicitly outside the configured namespace or the sentence refers only to the independent `judge_pair` scorer. State which interpretation is intended.
   - The closed-namespace examples are SKU codes, employee numbers, and inventory ids (`:62-65`), but the next paragraph calls identifier T0 a different, better design (`:71-75`) and the cautions say identifier-shaped T0 must not reuse this flag (`:95-97`). A cold reader cannot yet tell what remains in scope for name-lemma T0 after identifiers are separated. Before adoption, define the boundary and require uniqueness after `normalized_lemma` normalization, not merely uniqueness of raw identifiers.

## P0/P1 findings

None. The PR records an alternative without changing the accepted D95 behavior or WP-I.5 scope.

## Nits

- `optional-exact-t0-accept.md:74-75` says D20 “already treats external authority as an accelerator.” D20 actually rejects a third-party authority tier and records internal/domain authoritative ids as a future documented alternative. Rewording that sentence would keep the cross-reference exact.
- Prefer “deployment” or “store” over “tenant” at `optional-exact-t0-accept.md:62,66` to match the repository's single-deployment boundary.
- “Remain in the code” at `optional-exact-t0-accept.md:25-27` is the operator's question, but it momentarily jars with “not implemented” and the later instruction not to ship the switch. “Be retained by a future implementation” would remove the momentary ambiguity.
- “Collisions peak at scale” in the alternatives table is stronger than the analysis proves. “Collision probability/opportunity increases with scale” is the precise claim used elsewhere.

## Verification

Reviewed `origin/main...origin/feat/d95-t0-exact-opt-in-proposal` with Git, including all seven changed files. `git diff --check` is clean. No implementation or test execution was needed for this design-only PR.
