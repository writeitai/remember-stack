# Why the generic-identifier guard was removed

**Status:** analysis (non-binding). Supports D102.
**Date:** 2026-08-31.
**Code inspected:** engine `main` at `41b28780` (v0.9.0), `src/rememberstack/spine/resolver.py`.

## The question

D21 adopted a Senzing-style "promiscuous signal" guard: a string that links
to many distinct entities has demonstrated it does not identify anyone, so
stop trusting it as a match signal. The intent is sound and the failure it
targets is the worst one a memory system has — welding two strangers into
one entity, which D59/D65 identify as corrupting stance memory.

The question here is narrower: did the mechanism *as built* serve that
intent, or did it cost recall for no benefit?

## What the implementation actually did

`generic_identifier_guard` held one row per `(deployment_id,
normalized_lemma)`. `refresh_generic_identifier_guard` recounted on every
alias write:

```sql
SELECT :deployment_id, :lemma, COUNT(DISTINCT entity_id),
       COUNT(DISTINCT entity_id) >= :floor, 'promiscuous-lemma', now()
FROM aliases
WHERE deployment_id = :deployment_id AND normalized_lemma = :lemma
```

with `distinct_floor = 2`. The flag was consumed in exactly one place,
`_T1_T2_BLOCK`, and in exactly one way — ordering:

```sql
ORDER BY is_downweighted, coalesce(t1.score, 0.0) DESC, entities.entity_id
```

plus, per entity, `DISTINCT ON (entity_id) ... ORDER BY entity_id,
is_downweighted, score DESC`.

Three facts follow directly from that, and each was verified against the
code rather than inferred:

1. **It never touched exact matches.** `_candidate_snapshot` returns the
   exact tier whenever it is non-empty and only falls through to the fuzzy
   blockers when it is empty; `_T0_CANDIDATES` never joined the guard. So
   the intuitive worry — "ten Jan Nováks all get flagged and become
   unreachable" — was not what happened. An incoming `Jan Novák` matched
   T0, and all ten were returned.

2. **It outranked score.** `is_downweighted` was the *primary* sort key.
   In the fuzzy tier a 0.95 trigram hit on a shared name sorted below a
   0.31 hit on an unshared one, and with `blocking_limit = 10` the good
   candidate could be truncated out of the list entirely. The inner
   `DISTINCT ON` repeated the inversion one level down: an entity was
   represented by its best *unflagged* alias rather than its best alias,
   understating its score.

3. **It was never adjudication evidence — but it was not inert.** The flag
   existed only in those `ORDER BY` clauses. It was not on
   `ResolutionCandidate`, not in the recorded decision features, and never
   seen by T3 or T4. It is tempting to conclude from that it "reached no
   decision", and that conclusion is wrong. `_T1_T2_BLOCK` read it on every
   fuzzy resolution, and candidate ordering is decision-relevant twice over:
   the bounded prefix is truncated at `blocking_limit`
   (`resolver.py:432`), and T4's prompt instructs the model to prefer the
   first candidate in the supplied relevance order (`resolver.py:89`). An
   omitted candidate cannot be rescued by T3, T4, or an exclusion edge.
   The accurate statement is that the flag influenced *which candidates were
   adjudicated and in what order*, while never being *shown to* the
   adjudicator as evidence — so its effect was invisible in the decision
   record it helped produce.

## Why the counter was measuring the wrong thing

`COUNT(DISTINCT entity_id)` counts **entity rows**, not people. D95 forbids
T0 from auto-merging, so the resolver deliberately mints a second row when
the same real person is seen again under the same name and the evidence has
not yet justified a merge. The guard then read that second row as evidence
the name is generic.

So the mechanism could not distinguish the two situations it most needed to
tell apart:

- one real person recorded twice, awaiting adjudication (the normal,
  intended D95 state), and
- two unrelated people who happen to share a name.

Both look like `distinct_entity_count = 2`. Both were flagged. And in both
cases flagging made the adjudication *harder*, because it demoted exactly
the candidates T3/T4 needed to see. D21's own wording was "an alias that
**suddenly links many**"; a floor of two is not many, it is the base case.

## The ordering pathology this also fixes

Once every candidate in a fuzzy block matched through the same flagged
lemma, they all tied on `is_downweighted`, then tied on trigram score
(same alias, same query), leaving `entities.entity_id` — a random UUID — as
the effective tiebreak. Candidate order was therefore nondeterministic
whenever the interesting case arose, which is also why the guard's own
regression test passed or failed depending on which UUIDs were minted.

Replacing the flag with a real signal fixes both problems at once:

```sql
ORDER BY coalesce(t1.score, 0.0) DESC,
         similarity(entities.normalized_name, :lemma) DESC,
         entities.created_at,
         entities.entity_id
```

The new middle key asks how close the entity's *own canonical name* is to
the query. When two candidates matched through the same alias and tie on
score, the one whose identity actually resembles the mention wins.
`created_at` follows it so that an exact tie on similarity resolves to
"oldest first" — the convention `_T0_CANDIDATES` already uses — instead of
falling through to a UUID. Measured: the two ordering proofs passed 12–15
consecutive runs with freshly minted UUIDs each time.

**This costs measurable time.** `similarity()` is computed over the filtered
candidate set, and the `ix_entities_name_trgm` GIN index does not serve an
`ORDER BY`. On a deliberately pathological shape — 100,000 active entities
all sharing one fuzzy alias — three independent runs measured **+3.3%,
+12.5% and +31%** warm-median (e.g. 455 ms → 512 ms), with no new scan node.

The spread is worth taking seriously rather than averaging away: this is CPU
and sort work layered on a query that is already slow, so it moves with
machine and cache state. What all three runs agree on is the sign. The
honest claim is "a real but modest regression, concentrated in exactly the
overflow case", and any single percentage quoted from one run would be
overprecise.

The trade is accepted: that shape is already degenerate, and this key is
precisely what keeps the correct referent inside the truncated prefix.

### What `created_at` does and does not mean

It is a deterministic ordering key, not a claim about correctness. Its limits
are worth stating so nobody later reads more into it:

- **Rows written in one transaction tie.** PostgreSQL's `now()` is
  transaction-stable, so a bulk import gives every entity the same timestamp
  and ranking falls through to `entity_id`. The final key is therefore not
  decorative — in bulk-loaded deployments it decides routinely.
- **Backfills rank by backfill time**, not by when the entity was really
  first seen, unless the original timestamps are carried across.
- **Restore/replay is stable only if `created_at` is preserved**; a restore
  that re-mints rows re-orders them.
- **Merge/unmerge preserves priority** while the original rows and timestamps
  survive, and changes it when an entity is re-minted.
- **"Oldest first" is an incumbency preference.** It says the row has been
  around longer, not that it is the more likely referent.

None of this argues for another mechanism. It argues for reading the key as
what it is: a stable, explainable ordering that beats a random UUID, chosen
because it matches what `_T0_CANDIDATES` and the clusterer already do. If
ordering quality above the score/similarity keys ever turns out to matter,
that should be driven by a measured D22 result, not by adding a signal on
intuition — which is the mistake the guard itself made.

## What replaces it

Nothing at the blocking stage, deliberately. D21's real concern is served
by the mechanisms that adjudicate identity rather than rank candidates:

- **T3 profile evidence** — two people sharing `info@company.com` have
  different profiles; that is where the distinction lives.
- **T4** — one joint, binary, match-biased decision over the bounded
  candidate set (D100), with the candidates it needs actually present.
- **`resolution_exclusions`** — D21's cannot-link edges, which record
  "these two are NOT the same" durably and *are* consulted by clustering.
- **D95** — T0 never auto-merges, so a shared exact name is already
  prevented from collapsing two entities without adjudication.

The distinction worth keeping is that demotion at blocking time removes a
candidate from consideration, whereas all four of the above make a
*decision* about it. The guard was doing the former while D21 argued for
the latter.

That argument has a real limit, and it should be stated rather than papered
over: **none of these four can rescue a candidate that blocking never
returned.** They adjudicate the bounded prefix; they do not widen it. So
"T3/T4 replace the guard" is true for candidates that reach adjudication and
false for candidates truncated before it. The claim this analysis actually
supports is narrower — that demoting *every* shared name to protect against
a rare generic one was the wrong trade, not that truncation has ceased to
matter. What keeps that honest in practice is `search_complete`, which tells
the decision its candidate set was incomplete, and the overflow canary
below, which pins the behaviour under exactly that pressure.

## What we gave up, honestly

One real function is lost. When a fuzzy block overflowed `blocking_limit`,
the flag caused candidates matched only by a promiscuous string to be
truncated first, which is a sensible truncation priority.

It is worth being precise about which case that actually was, because the
obvious example is wrong. An `info@company.com` matched **exactly** never
reached the guard at all — it takes T0, which the guard never touched. The
lost case is narrower: a *near* match on a generic string, where more than
`blocking_limit` entities share it, so the block must truncate and the
promiscuous matches are no longer pushed to the back of the queue.

`test_generic_alias_overflow_keeps_the_resembling_referent` is the canary
for exactly this shape — thirteen entities answering to one generic alias,
queried with a near variant. It pins the two properties that make the loss
tolerable: `search_complete` is `False`, so the resolver does not pretend it
saw everything, and the entity whose own canonical name resembles the query
still ranks first and survives the cut. Under the guard, every one of those
candidates was flagged, so the ten survivors were selected by random UUID.

This is accepted because the case needs both a genuinely generic string
*and* an overflowing candidate list, because truncation stays visible to the
decision, and because the cost removed — an inverted primary sort key on
every fuzzy resolution — applied far more often. If measurement later shows
role addresses are a real problem, the right shape is a low-priority
tiebreak *after* score, or an eligibility rule that stops such strings
becoming aliases at all, not a primary sort key with a floor of two.

## Cost removed

`refresh_generic_identifier_guard` ran a `COUNT(DISTINCT entity_id)` on
`aliases` on every resolve (twice when the source lemma differed from the
canonical one). That value *was* read back by `_T1_T2_BLOCK` on later fuzzy
resolutions — the write was not dead code — but with the ranking input gone
there is no remaining consumer, so the write goes with it. The table also retained
per-deployment surface strings with no lineage provenance, which hard-forget
had to blanket-delete precisely because it could not prove what any row came
from. Dropping the table removes both.

## Alternatives considered

- **Raise the floor** (say to 10). Keeps a mechanism that still counts
  entity rows rather than people, and still outranks score once it fires —
  so above the floor the inversion is unchanged. It moves the threshold
  without fixing what the threshold gates.
- **Demote to a tiebreak after score.** Preserves the truncation-priority
  benefit and removes the inversion. Rejected for now on leanness grounds:
  it retains a hot-path write and a table for a benefit not yet measured.
  This is the first thing to reach for if role addresses prove to be a
  problem in practice.
- **Keep the table, stop reading it.** Leaves a written-but-unread table
  and a per-resolve count for nothing.
- **Keep the flag, fix only the sort order.** Considered and implemented as
  an intermediate step; superseded within the same change once it was clear
  that with the ranking input gone the flag had no remaining consumer, so
  the per-resolve write and the table were paying for nothing.
