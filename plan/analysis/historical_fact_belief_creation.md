# Recording a fact after its source was withdrawn

Status: analysis supporting the narrow historical-creation clarification in the
temporal design. This analysis is not itself binding. Independent read-only
analyses by `historical_belief_a` and `historical_belief_b` on 2026-09-07 agree
on an empty belief interval and the need to qualify the existing design wording.

## The observed conflict

The PostgreSQL observation execution proof
`test_atomic_withdrawn_source_keeps_historical_receipt_without_live_nomination`
creates a fact after a recorded D55 source withdrawal. The prepared creation time
is later than the source event. The planner seeds `ingested_at` at that recorded
creation instant, then copies the earlier withdrawal into `invalidated_at`.
The existing database check correctly rejects the inverted belief interval.

The same case can occur during a late state re-split: a retained historical
assertion receives a new observation identity after its source stopped being
current. D113 requires the original application receipt and the separately owned
current evidence assignment to survive; omitting the identity is not a valid
completion strategy.

Sources:

- `plan/designs/temporal_clocks_design.md` §4.4: ordinary D55 belief closure at
  the persisted reconciliation instant; no world-time fallback to the clock.
- `plan/designs/temporal_write_and_lifecycle_design.md` §4: D54/D55 separation,
  persisted replay and preservation of independent caps.
- `plan/designs/observation_temporal_application_design.md` §§4–6: atomic new
  identities, immutable original receipts, support relocation and erasure.
- `src/rememberstack/spine/migrations/versions/p0_02_0004_claims_facts_evidence.py`:
  relation and observation `invalidated_at >= ingested_at` checks, explicitly
  described as prohibiting unlearning before learning.
- `src/rememberstack/spine/observation_planning.py`,
  `_close_withdrawn_creations`: the historical creation path that exposed this
  conflict when connected to actual database execution.

## Decision and alternatives

A newly created identity supported only by already withdrawn D55 testimony was
never a current system belief. Represent that with an empty belief interval:
for this creation path, close at the later of its recorded creation and recorded
withdrawal instants. When withdrawal precedes creation, both belief endpoints
equal the creation instant. The source event retains its original time and
cause in the existing prepared/journal provenance; it is not relabelled as a
later source withdrawal.

Both independent analyses recommend this representation. One suggested applying
the lower bound to any first closure; the narrower recommendation is selected:
this clarification applies to newly materialized historical identities. Ordinary
existing-fact lifecycle writes retain their existing D55 event-time contract.
An inconsistent existing history must be investigated rather than silently
clamped. Existing invalidation timestamps remain immutable under this rule.

Rejected alternatives:

- Backdating ingestion invents an earlier system registration and obscures the
  distinction between source history and system belief history.
- Removing the database check permits negative belief intervals.
- Leaving the new fact live creates precisely the unsupported current belief
  that D55 forbids, including a state that could activate later.
- Dropping the historical identity loses evidence/application history required
  by D113.
- Calling `now()` at execution/retry makes the recorded result depend on the
  replay day. The existing prepared creation time is already sufficient.

## Costs, failure behavior and acceptance

This needs no schema change, new clock, queue, configuration or storage system.
It qualifies one creation-path rule. World-time seeding, occurrence metadata,
cap authority and D54 re-extraction flags remain governed by their existing
contracts. The seed, empty belief interval, evidence and application receipt
commit together; no intermediate current fact is observable. A failed group
rolls back, and retry/replay consumes the exact recorded timestamps.

Acceptance requires a real source withdrawal followed by creation: the fact
has an empty belief interval, retains its source world dates and evidence,
does not appear in live nomination, and reuses its receipt exactly. Also cover
late re-split creation, unchanged ordinary existing-fact closure, D54 flagging,
and retention of the original withdrawal cause/time. The changed provenance
uses existing source-bearing JSON and remains in D74's scrub inventory.
