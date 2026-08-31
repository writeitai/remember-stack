# Binary, match-biased T4 identity adjudication

**Date:** 2026-08-31

**Status:** analysis; non-binding evidence for D100 and the D95 amendment

## Question

The D99 resolver represents uncertainty honestly, but its operational response
to uncertainty is harmful: every `insufficient_evidence` result may create one
more active entity. Should the identity residue instead use one bounded model
call that must select an existing candidate or create a new entity, with an
explicit bias toward reuse?

File attribution and source-local entity identity are outside this analysis.
They are independent inputs that a later accepted contract may add to the same
resolver without changing the decision shape considered here.

## Evidence

The completed RememberStack v0.7.3 LoCoMo `conv-26` run used D99 on all 19
sessions. It finished with zero active work and zero dead letters, so the
identity result is not an incomplete-drain artifact. The store contained:

| Diagnostic | Result |
| --- | ---: |
| Active entities | 610 |
| Active entities named `Caroline` | 313 |
| Active entities named `Melanie` | 268 |
| Current resolution decisions | 1,005 |
| T3 accepts | 0 |
| T4-small links | 394 |
| T4-frontier links | 1 |
| T4-small mints | 587 |
| Provisional `insufficient_evidence` mints | 581 |
| Decisions with incomplete candidate search | 942 |

The run is recorded at the immutable cloud-repository commit
[`8c23b89`](https://github.com/writeitai/ultimate-memory-cloud/blob/8c23b89fca1a492401875bfd24f5702494a0de77/design/analysis/locomo-conv26-d99-validation-2026-08-28.md)
(retrieved 2026-08-31).

The model often received two compatible but topically different descriptions,
for example an incoming adoption claim and a candidate profile dominated by
art or counseling. D99 correctly refused to call absence of overlap positive
difference. The write path nevertheless turned that uncertainty into another
active candidate. More candidates disabled T3, bounded the visible set, and
made the next uncertain decision harder. Convergence later proposed only small
semantic-profile components because one person's unrelated life topics need
not be close in embedding space.

The problem is therefore not merely the word `insufficient_evidence`. The
system's error policy makes absence of proof create another served identity.

## Alternatives

| Alternative | Disposition | Reason |
| --- | --- | --- |
| Keep D99 unchanged | Reject | Safe uncertainty still grows the active candidate set linearly. |
| Increase candidate or T4-call limits | Reject | Raises latency and provider spend without defining which error direction the system prefers. |
| Keep tri-state but give uncertainty a separate unresolved store | Reject for this decision | Adds another identity state and serving path when a simpler complete-system policy can be measured first. |
| Keep pairwise small→frontier adjudication | Reject | Up to three isolated calls cannot compare the candidate set jointly; confidence-based escalation adds a second seat without adding authoritative evidence. |
| One binary call, neutral prompt | Reject | A forced but unspecified tie becomes model/order noise. |
| One binary call, match-biased prompt | Choose | Makes the intended error preference explicit, bounds spend to one call, compares candidates jointly, and prevents topic diversity alone from minting identities. |
| Exact-name T0 auto-accept | Reject | Same-name father/son and colleague cases never reach evidence-bearing adjudication. |

## Chosen policy

T0–T2 continue to generate a bounded candidate set. Conservative T3 remains
unchanged: it may accept only one sufficiently strong, current-profile
candidate. The residue reaches exactly one T4 call on the configured simple
model.

T4 receives the incoming canonical name and claim plus every candidate in the
bounded resolver snapshot. Each candidate carries its canonical name, source
aliases, current profile description, current salient facts, and T3 score or
gate. Candidate, distinct-alias, and profile-fact limits bound the prompt; “all
relevant information” does not mean an unbounded registry-history dump.

The output is exactly one of:

- `match(candidate_id)`; or
- `new`.

There is no `insufficient_evidence`, confidence-routing branch, or frontier
seat. Ambiguity collapses toward `match`: compatible facts and different topics
favor the best existing candidate. `new` requires positive evidence that the
incoming referent differs from every supplied candidate. When several
candidates remain compatible, T4 selects the first in the deterministic
T3-relevance order. A candidate-free complete block still mints without T4.

The append-only decision retains candidate order, search completeness, T3
scores/gates, chosen candidate or `new`, rationale, model, and resolver
generation. A `new` decision may create supported-different exclusions only
for the supplied candidates because the prompt's `new` contract is positive
distinction from that set. Candidate completeness remains diagnostic; it does
not reintroduce a third runtime outcome.

D99's snapshot → unlocked provider call → locked revalidation, bounded retry,
T3 diagnostics, profile refresh, convergence nomination, reversible merges,
and review guards remain. Existing tri-state decisions remain audit history and
are never rewritten. `T4_small` continues to identify the configured
simple-model seat; `resolver_version` and features distinguish historical
pairwise decisions from the new joint call. `T4_frontier` remains readable but
is no longer written by identity resolution.

## Costs and failure posture

The deliberate trade is more false-merge risk in exchange for substantially
less false splitting. A false merge can contaminate profiles and retrieval, so
same-name negative canaries remain hard release gates. Merge and mention
decision history remains reversible and auditable. Provider failure remains a
visible worker failure; it is not converted into `new` or a silent candidate
choice.

The one-call contract is cheaper than today's up-to-three pairwise calls and
has no frontier escalation. Prompt size grows because candidates are compared
together, but remains bounded by configuration. Candidate order and structured
output are deterministic inputs; model behavior is evaluated rather than
assumed.

## Acceptance evidence

- Same-name, compatible but topically disjoint facts select the existing
  entity rather than `new`.
- Explicit father/son and same-name-colleague evidence creates a new entity.
- Candidate permutations that preserve T3 ordering produce the same choice.
- One T4 provider call occurs per residue decision, including multiple
  candidates; no frontier call exists.
- T4 receives every candidate in the bounded snapshot with its current profile
  description, salient facts, aliases, and T3 diagnostic.
- An incomplete candidate snapshot still produces one binary decision and
  records `search_complete=false` without provisional authority.
- Snapshot mutation during the unlocked call invalidates and retries the
  result; stale selection never commits.
- Existing D99 audit rows remain readable after the application cut.
- A deterministic repeated-name workload does not grow one active entity per
  compatible mention, while the same-name negative canaries remain separate.
