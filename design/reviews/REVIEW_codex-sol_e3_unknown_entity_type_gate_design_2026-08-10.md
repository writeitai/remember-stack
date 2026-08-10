# Adversarial review: E3 unknown entity type gate

**Reviewer:** Codex (`codex-sol`)  
**Date:** 2026-08-10  
**Reviewed:** `plan/analysis/e3_unknown_entity_type_gate_analysis.md`,
`plan/designs/e3_unknown_entity_type_gate_design.md`, both entries currently
numbered D85 in `decisions.md`, and the E3/entity-resolution implementation paths.

## Verdict

**Request changes.**

The product policy is correct: retry the claim normalizer once, then drop only
the final-response assertions that still carry illegal entity types. Do not
coerce to `Concept`, do not auto-register the model's label, and do not fail the
document-version job for this soft failure. That is the smallest response that
preserves the closed ontology without turning a recoverable structured-output
error into a document-wide outage.

The binding design is not implementation-ready yet. Its defense-in-depth hook
does not cover the resolver E3 actually calls; its fallback explicitly still
allows a job failure; the metrics do not define coherent numerators,
denominators, or a concrete emission path; a second normalize call can be
silently omitted from cost accounting; and the design does not version the new
generation contract or give a provenance-correct DLQ replay procedure. There is
also already an unrelated binding D85, so the new decision's identity is
ambiguous.

## P0 findings

None.

## P1 findings

### P1.1 — The specified defense-in-depth hook is not on E3's active mint path, and the fallback still permits the forbidden document failure

The design assigns an optional typed guard to
`EntityRegistry.resolve_t0` (`plan/designs/e3_unknown_entity_type_gate_design.md:89-95`).
That method does have a mint insert
(`src/rememberstack/spine/entity_registry.py:42-75`), but E3 does not use it for
relation or observation resolution. E3 calls `self._resolver.resolve` for both
relation endpoints and observation subjects
(`src/rememberstack/workers/e3.py:273-287`,
`src/rememberstack/workers/e3.py:318-327`). The injected `EntityRegistry` is used
there only to read replay markers (`src/rememberstack/workers/e3.py:140-146`).
The active resolver is `CascadeResolver`, whose separate `_mint` writes the
entity type directly (`src/rememberstack/spine/resolver.py:85-155`,
`src/rememberstack/spine/resolver.py:430-448`). Guarding only
`EntityRegistry.resolve_t0` therefore does not defend the incident path.

The failure table compounds this by allowing an escaped FK to "fail claim path
or job" (`plan/designs/e3_unknown_entity_type_gate_design.md:134-144`). There is
no claim-level exception boundary today: any exception leaving
`_normalize_claim` reaches the worker's document-work boundary and schedules a
whole-job retry/DLQ (`src/rememberstack/workers/base.py:202-249`). "Fail claim
path" is not an available behavior until the design defines it. Allowing "or
job" directly contradicts the mandatory rule at design lines 22-25.

**Concrete fix:** make the pre-resolve response filter authoritative, then add
one shared type-membership check to every E3-reachable mint implementation,
including `CascadeResolver._mint` (and `EntityRegistry.resolve_t0` if it remains
a supported write path). The check must run before the entity insert and raise a
specific `UnknownEntityTypeError`, not expose `IntegrityError` as normal flow.
Catch only that typed error at the relation/observation assertion boundary,
record it as a gate miss, drop that assertion, and continue. Database outages,
unrelated integrity violations, and other systemic failures must still escape.
Rewrite the failure table to make the split unambiguous: absent type -> typed
soft drop; inability to query the registry or unrelated FK/schema failure ->
systemic work failure. Add an acceptance test through the real
`CascadeResolver`, not only a mocked or T0-only registry.

### P1.2 — The metrics contract cannot produce the rates it promises

The algorithm emits `unknown_type_event` once for every illegal attempt, but
emits recovery once per claim and terminal drop once per assertion
(`plan/designs/e3_unknown_entity_type_gate_design.md:57-75`,
`plan/designs/e3_unknown_entity_type_gate_design.md:117-129`). Those are
different units. A persistent illegal claim produces two unknown events and may
drop several relations/observations, so `recovered / unknown_type_events` and
`dropped / unknown_type_events` are not stable rates and can mislead. The
specified events also provide no `claims_processed` event/counter even though it
is the first denominator. The analysis additionally requires a rate per
normalizer LLM call (`plan/analysis/e3_unknown_entity_type_gate_analysis.md:79-80`),
which the binding design omits.

"Structured logs (and preferably worker/run counters)" is also not a complete
emission contract. E3 has only a provider-cost meter; it does not accept a
metrics/telemetry port, and `HandlerOutcome` carries no metric attributes. The
design neither selects an existing sink nor specifies the structured-log schema
needed for reliable aggregation.

**Concrete fix:** freeze metric units and a concrete sink before coding. At
minimum, emit one structured `e3.normalize_summary` per processing attempt with:

- `deployment_id`, `processing_id`, work-ledger `attempt`,
  `normalizer_version`, and model;
- `claims_considered`, `claims_normalizer_called`, and `normalize_calls`;
- `claims_with_unknown_type`, `unknown_type_attempts`, `retry_calls`,
  `recovered_claims`, and `terminal_dropped_claims`;
- `dropped_relations` and `dropped_observations` as separate assertion counts;
- a bounded top-k map of illegal type strings.

Define the rates as, for example, incident claim rate =
`claims_with_unknown_type / claims_normalizer_called`, per-call violation rate =
`unknown_type_attempts / normalize_calls`, recovery rate =
`recovered_claims / claims_with_unknown_type`, and residual claim-drop rate =
`terminal_dropped_claims / claims_normalizer_called`. Keep per-claim warning
events for diagnosis, but name `inner_attempt` separately from the work-ledger
attempt and do not use raw illegal strings as unbounded metric labels. State how
attempt summaries are deduplicated or intentionally counted when a whole work
item is retried.

### P1.3 — Inner attempts need distinct cost-ledger keys or the retry spend disappears

The current normalizer call is metered as `normalize:<claim_id>`
(`src/rememberstack/workers/e3.py:224-241`). Cost records are idempotent on
`(processing_id, work attempt, call_key)` and silently ignore a duplicate key
(`src/rememberstack/spine/work_ledger.py:466-512`,
`src/rememberstack/spine/work_ledger.py:893-905`). If implementation reuses the
existing key for the retry, the provider bills two calls while the cost ledger
records one. That makes both cost governance and the required per-call failure
rate wrong.

**Concrete fix:** specify deterministic, unique inner-attempt keys such as
`normalize:<claim_id>:inner:1` and `normalize:<claim_id>:inner:2`; meter each
successful call immediately. Define how a usage-bearing provider failure on the
retry is keyed as well. Add an integration assertion that both call rows, token
totals, and costs are present for a recovered and a terminal-drop case.

### P1.4 — The behavior and retry prompt require a new component version, but replay is specified against the old dead-letter row

The retry suffix changes the model input, and the gate changes which output can
land. Nevertheless, the implementation checklist does not require an
`E3_NORMALIZER_VERSION` bump or a corresponding prompt/parameter registration
(`plan/designs/e3_unknown_entity_type_gate_design.md:162-168`). E3 stamps that
constant on relations (`src/rememberstack/workers/e3.py:307-315`), and
`processing_state` treats component version as part of the work identity
(`src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py:75-95`).
Running new behavior under the July version would make provenance false.

The instruction to replay the existing BEAM dead letter creates a second
ambiguity. Reopening the same row preserves its old component version, while a
new-version enqueue creates a distinct work identity. The design must choose;
"should complete" does not define a provenance-correct operator action.

**Concrete fix:** mint and register a new E3 normalizer/component version that
captures the base prompt, retry suffix, temperature, and maximum inner attempts.
Specify whether incident recovery (a) explicitly replays the legacy work row
under a documented bug-fix compatibility exception or (b), preferably, enqueues
the same target/payload under the new component version while retaining the old
dead letter for audit. State the readiness and duplicate-evidence behavior for
that choice and test it end to end.

### P1.5 — The new ADR collides with the existing binding D85

`decisions.md` now has `D85. E3 unknown entity types` at line 13 and the earlier
`D85. The full-system LoCoMo answer seat` at line 3303. The LoCoMo binding design
already refers to D85. A cold reader cannot use "D85" as a stable decision
identity, and tooling that assumes unique decision numbers will select one
arbitrarily.

**Concrete fix:** renumber this new decision to the next unused identifier
(`D86` at review time), place it in sequence, and add `Decision: D86` to the E3
design header. Update any references in the same change. Do not renumber the
older LoCoMo decision, which already has inbound references.

## P2 findings

### P2.1 — Freeze whole-response replacement and assertion-counting semantics

The pseudocode overwrites `response` on retry, which implies that the second
response completely replaces the first (`plan/designs/e3_unknown_entity_type_gate_design.md:57-73`).
That is the safer rule, but it is not stated normatively. A developer could
instead retain legal first-response candidates and merge them with the retry,
creating duplicate or mutually inconsistent assertions. The terminal metric is
also ambiguous when one relation has two illegal endpoints or several illegal
labels: it must be one dropped relation, not one drop per bad field.

**Concrete fix:** state that each retry is a full re-normalization; only the
final response is eligible for gates and writes; no assertion from an earlier
response is retained. Filter final relations if either endpoint type is illegal
and final observations if the subject type is illegal. Count dropped assertions
once each, while counting illegal-label occurrences separately if desired.

### P2.2 — "Allowed" does not say whether deprecated/inactive registry rows are legal

The design says every registry key is allowed
(`plan/designs/e3_unknown_entity_type_gate_design.md:42-53`). The current
`entity_type_parents` query also returns every row without a status predicate
(`src/rememberstack/spine/fact_catalog.py:377-383`,
`src/rememberstack/spine/fact_catalog.py:512-516`), although `entity_types` has a
status lifecycle and predicate prompts already filter to active predicates. A
deprecated pack type can therefore remain in the prompt and pass the new gate.

**Concrete fix:** explicitly choose active rows or all FK-present rows. Closed
ontology semantics strongly suggest `status = 'active'` for prompt emission and
normalizer acceptance, while existing entities of a deprecated type remain
readable/resolvable. If that is the choice, define the post-resolution behavior
when an alias resolves to an existing deprecated type and add a test.

### P2.3 — Illegal strings need bounds before they are echoed or aggregated

`EntityRef.type` is only constrained to be non-empty
(`src/rememberstack/model/relations.py:10-19`). The retry suffix echoes illegal
strings and the observability plan records/top-ranks them. A malformed model
response can therefore create oversized retry prompts/log records, log-control
characters, and unbounded metric cardinality.

**Concrete fix:** cap the number and rendered length of illegal values in the
retry suffix and diagnostic logs, escape control characters, retain a hash plus
bounded sample when truncated, and keep raw strings out of metric label sets.

### P2.4 — The inner budget is per work attempt unless zero-fact outcomes become durable

The current replay marker comes only from landed relation or observation
evidence. A claim that terminally drops every assertion has no marker and is
normalized again if a later claim causes the version work item to retry
(`src/rememberstack/workers/e3.py:140-157`). Thus `MAX_INNER_ATTEMPTS = 2` is
cost-bounded only within one work-ledger attempt, not across the document's
outer retries, and raw log-derived rates can double-count it.

**Concrete fix:** document this as the v1 replay contract and include the outer
attempt in metrics, or persist a versioned zero-fact normalization outcome so a
soft-dropped claim can be skipped on replay. The latter is broader and need not
block this incident fix, but the design must not call the budget globally
cost-bounded without the qualification.

## Retry-then-drop versus coercion

**Retry-then-drop is the correct policy.**

Coercing an unknown label to `Concept` changes an uncertain model output into an
apparently valid ontology assertion. On a new lemma that can permanently mint
the canonical entity under the wrong type; later exact resolution returns the
stored type, so the damage propagates beyond the original assertion. It also
removes the failure signal operators need to detect prompt/model drift.

One constrained retry is proportionate because the failure is expected to be
rare, the prompt can explicitly correct the finite vocabulary, and the cost is
local to one claim. After the retry, dropping only illegal-bearing assertions
keeps the graph semantically honest and retains the immutable source claim for
future re-derivation. Dropping the whole claim response would unnecessarily
lose legal sibling assertions; auto-registering the label would defeat D18;
failing the version would repeat the incident.

## Metrics completeness

**Incomplete as written.** The event names are useful diagnostic vocabulary,
but they do not yet satisfy the product requirement to track failure rates.
Approval requires:

1. exact units for claim, attempt, call, illegal-label, and dropped-assertion
   counters;
2. explicit denominators, including claims actually sent to the normalizer and
   total normalize calls;
3. a concrete structured-log or telemetry sink with `processing_id`, outer and
   inner attempt dimensions, model, and normalizer version;
4. unique cost-ledger keys for both calls;
5. bounded top-label handling and a stated outer-retry/deduplication policy;
6. an operator query/dashboard proof using a fixture with recovered and
   persistent failures.

The proposed health bands should be applied to
`terminal_dropped_claims / claims_normalizer_called` (and optionally separate
relation/observation assertion rates), not to the currently ambiguous
`dropped / unknown_type_events` ratio.

## Test plan gaps

The four listed tests are necessary but insufficient. Add these acceptance
cases:

1. Persistent illegal **observation** subject: exactly one retry, no resolver or
   observation adjudicator call for that assertion, job succeeds, terminal
   branches enqueue.
2. Persistent illegal relation through the real `CascadeResolver`: no entity,
   alias, mention, profile, relation, or evidence for the illegal assertion and
   no document retry/DLQ.
3. Several claims in one version: an early persistent illegal claim, a later
   legal relation, and a legal observation all complete; downstream stages are
   enqueued and the processing row is `succeeded`.
4. Mixed final response across relations and observations: legal siblings land;
   candidates with an illegal subject or object drop once each; multiple illegal
   labels trigger one retry for the claim, not one retry per label/assertion.
5. First response contains legal and illegal assertions, retry response differs:
   prove that only the complete second response is eligible for writes.
6. Illegal type on a relation that would also fail predicate or signature gates:
   prove the type retry runs first, as the binding order requires. For
   `other:*`, prove a terminally type-invalid relation does not register an
   otherwise-unused escape predicate as a side effect.
7. Case sensitivity and exactness: `Process`, `process`, trailing whitespace,
   and a valid extension-pack type; add deprecated/inactive coverage after
   P2.2 is resolved.
8. Cost accounting: two distinct normalize call rows with correct summed usage
   for retry success and retry drop; one row on the all-legal path.
9. Metrics: exact summary counters/rates for all-legal, recovered, terminal
   relation drop, terminal observation drop, and multiple illegal labels;
   verify bounded rendering/cardinality.
10. Retry-call provider failure remains a systemic work-ledger retry and does
    not get mislabeled as a terminal unknown-type drop; usage-bearing failures
    remain accounted.
11. Defense-in-depth race/gate miss: the registry check reports a typed soft
    error at the actual mint path and E3 continues the next assertion/claim;
    an unrelated integrity error still fails the work item.
12. Outer work replay after an earlier terminal soft drop: verify the documented
    zero-fact replay and metric semantics.
13. Version/provenance and incident replay: new work carries the new E3 version;
    the chosen old-DLQ recovery procedure completes downstream branches without
    rewriting the historical component identity.

The existing E3 chain tests prove legal relation/observation handling and
fact-backed replay, but do not cover any of these illegal-type or inner-retry
contracts (`src/tests/workers/test_e3_chain.py:478-523`).

## Implementation risks

- **Wrong resolver patched:** implementing only the table entry in design §6
  leaves `CascadeResolver._mint` as the active unguarded write path.
- **Exception over-catching:** catching generic `IntegrityError` would hide
  schema, tenancy, and unrelated FK defects. Only the explicit absent-type soft
  error belongs at the assertion boundary.
- **Partial assertion side effects:** relation endpoints resolve in separate
  calls. Filtering must happen before either call; relying on a late exception
  can mint/record the subject before the object fails.
- **Recall drift across retry:** a full second generation may omit legal facts
  from the first. Complete replacement is still the least ambiguous policy, but
  recovered/drop rates and a corpus check should quantify the effect.
- **Cost and budget undercount:** duplicate call keys silently erase the retry
  call from `cost_ledger`; outer retries can repeat zero-fact claims.
- **Ontology snapshot drift:** allowed types are loaded once per version. A
  concurrent registry lifecycle change needs the typed mint check to close the
  time-of-check/time-of-use gap and a defined active/deprecated policy.
- **High-cardinality observability:** raw model strings must not become metric
  labels or unbounded prompt/log content.
- **Provenance mismatch:** shipping under the old normalizer version or replaying
  an old-version row with unqualified new behavior makes audit records lie.

## Required changes before approval

1. Close P1.1 with the real resolver path and an explicit claim/assertion-level
   typed-error contract that cannot DLQ the document for an absent type.
2. Replace §8 with coherent metric units, denominators, sink, retry dimensions,
   and bounded label handling; specify unique cost keys.
3. Version the new generation/gate behavior and define provenance-correct DLQ
   recovery.
4. Renumber the new ADR to D86.
5. Freeze final-response replacement/drop semantics and expand the test plan to
   cover observations, mixed claims, real resolver behavior, job success,
   metrics/costs, and replay.

With those changes, the design should be approvable without changing the chosen
retry-then-drop product policy.
