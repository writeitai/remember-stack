# E3 unknown entity type gate — design (binding)

**Status:** accepted for implementation (pending dual design review on PR)  
**Date:** 2026-08-10  
**Analysis:** [e3_unknown_entity_type_gate_analysis.md](../analysis/e3_unknown_entity_type_gate_analysis.md)  
**Amends:** E3 behavior described in [e2_e3_claims_relations_design.md](e2_e3_claims_relations_design.md) §5 (normalization gates)  
**Ontology:** D18 closed entity vocabulary; core types in `CORE_MANIFEST`

## 1. Decision

When the E3 normalizer emits an entity type that is **not** in the deployment’s
`entity_types` registry:

1. **Do not mint** that entity under the illegal type.
2. **Do not coerce** to `Concept` (or any other type) under this design.
3. **Retry** the normalizer LLM call for that claim (inner budget) with an
   explicit closed type list reminder.
4. If still illegal after the budget: **drop** the offending relation and/or
   observation assertion, log, increment metrics, **continue** the version job.
5. **Track** unknown-type rates so operators can see recovery vs residual drop.

The document-version `normalize_relations` job **must complete successfully** when
only re-derivable soft failures remain (unknown type after budget, unknown
predicate, signature reject). It must **not** dead-letter solely because the LLM
invented a type label.

## 2. Problem (operator-facing)

A single illegal type (e.g. `Process`) caused a Postgres FK failure on `entities`,
failed the whole version-level E3 job, blocked observations and all later stages,
and prevented BEAM scoring despite successful Claimify on ~15k claims.

## 3. Scope

| In scope | Out of scope |
| --- | --- |
| Gate + retry + drop for unknown **entity types** on E3 normalize path | Auto-creating `entity_types` rows from LLM output |
| Per-claim inner re-call budget | Coercion to Concept |
| Metrics / structured logs for rates and top labels | Fan-out of normalize to per-claim work-ledger jobs (future proposal) |
| Keeping FK as safety net | Changing core ontology membership |

## 4. Allowed types

At the start of a version normalize (or once per claim if cheaper to pass through):

```text
allowed_types = set(entity_types for deployment)  # core + installed packs
```

Same set already used to build the normalize prompt (`types=", ".join(sorted(type_parents))`).

A type string is **illegal** if it is not exactly in `allowed_types` (case-sensitive
match to stored registry keys; no fuzzy match).

## 5. Algorithm (per claim)

```
response = generate(normalize prompt)   # attempt 1
for attempt in 1..MAX_INNER_ATTEMPTS:   # MAX_INNER_ATTEMPTS = 2 (attempt 1 + one retry)
    illegal = types_in(response) - allowed_types
    if not illegal:
        break
    record_metric(unknown_type_event, labels=illegal, claim_id, attempt)
    if attempt == MAX_INNER_ATTEMPTS:
        break
    response = generate(normalize prompt + retry_suffix(illegal, allowed_types))
    record_metric(unknown_type_retry)
if types_in(response) - allowed_types is non-empty after budget:
    # process response but DROP any relation/observation that still references illegal types
    record_metric(unknown_type_dropped, ...)
else:
    if any retry succeeded in clearing types:
        record_metric(unknown_type_recovered)
# existing gates: unknown predicate drop, signature reject, resolve/mint, upsert
# continue to next claim even if this claim produced zero facts
```

**Constants (v1):**

| Name | Value | Rationale |
| --- | --- | --- |
| `MAX_INNER_ATTEMPTS` | **2** | One free retry after first illegal set; cost-bounded |
| Terminal policy | **drop** | No coerce (product decision 2026-08-10) |

**Retry prompt suffix (normative intent):** remind that every `type` field must be
exactly one of the allowed tokens; list illegal tokens seen; list allowed types
again. Temperature remains 0.

## 6. Where to implement

| Layer | Responsibility |
| --- | --- |
| `E3Normalizer._normalize_claim` | Detect illegal types on `NormalizationResponse`; inner retry; drop illegal-bearing relations/observations before resolve |
| `EntityRegistry.resolve_t0` | Optional assert: refuse mint if type not in registry (defense in depth); raise a typed soft error only if called incorrectly |
| Worker metrics / logging | Counters or structured warning attributes (see §8) |

Do **not** treat FK `IntegrityError` as the primary control path. Catching FK and
retrying the whole job is insufficient and expensive.

## 7. Interaction with existing soft gates

Order of application on a single normalize response (after any inner retries):

1. Unknown **entity type** → drop that relation/observation (this design).
2. Unknown **predicate** → drop relation (existing).
3. **Signature** reject → drop relation (existing).
4. Resolve / mint / upsert (existing).
5. Observations adjudicated after all claims in the version (existing).

Illegal types must be filtered **before** resolve so T0 never attempts mint with
a non-registered type.

## 8. Metrics and observability

At minimum, structured logs (and preferably worker/run counters) for:

| Event | Fields |
| --- | --- |
| `e3.unknown_entity_type` | `claim_id`, `attempt`, `illegal_types[]`, `deployment_id` |
| `e3.unknown_entity_type_retry` | `claim_id`, `attempt` |
| `e3.unknown_entity_type_recovered` | `claim_id`, `attempts_used` |
| `e3.unknown_entity_type_dropped` | `claim_id`, `illegal_types[]`, `kind=relation|observation` |

Derived rates (ops dashboards / log queries):

- unknown_type_events / claims_processed  
- recovered / unknown_type_events  
- dropped / unknown_type_events  
- top illegal type strings  

**Health bands (guidance, not hard SLOs):** residual drop rate after retry  
≪ 0.5% of claims = noise; 1–5% = investigate prompt/model; &gt;5% = regression.

## 9. Failure / recovery

| Case | Behavior |
| --- | --- |
| Illegal type, recovered on retry | Claim normalizes; metric recovered |
| Illegal type, drop after budget | Claim may contribute zero facts; version job still succeeds |
| Provider outage / uncaught bug | Work-ledger fail/retry/DLQ as today |
| FK still fires | Bug: gate incomplete — fail claim path or job after logging; fix gate |

Replay of a version-level DLQ after this ships should complete without human
force-succeed.

## 10. Testing

- Unit: response with type `Process` → retry called once → legal second response applied.
- Unit: illegal type twice → relation dropped; no `INSERT` into entities with that type; no exception.
- Unit: mixed legal + illegal relations in one response after budget → legal kept, illegal dropped.
- Regression: all-legal path unchanged (no extra generate call).

## 11. Alternatives not chosen

| Alternative | Why not |
| --- | --- |
| Coerce to Concept | Product decision: prefer drop over silent type rewrite |
| Auto-insert entity_types | Violates closed ontology (D18) |
| Status quo (FK + job DLQ) | Document-level blast radius; blocks scoring |
| Per-claim work-ledger fan-out | Larger change; defer unless other error classes demand it |

## 12. Implementation checklist

1. Land this design after dual review (Claude + Codex).
2. Implement gate + retry + drop + metrics in E3; tests as in §10.
3. Dual review of implementation PR.
4. Replay BEAM host `normalize_relations` DLQ; confirm observations appear and
   later stages enqueue.
