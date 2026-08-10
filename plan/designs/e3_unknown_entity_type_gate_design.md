# E3 unknown entity type gate — design (binding)

**Status:** accepted for implementation after dual design review (fixes applied)  
**Date:** 2026-08-10  
**Decision log:** D86  
**Analysis:** [e3_unknown_entity_type_gate_analysis.md](../analysis/e3_unknown_entity_type_gate_analysis.md)  
**Reviews:**  
- [REVIEW_claude-opus_e3_unknown_entity_type_gate_design_2026-08-10.md](../../design/reviews/REVIEW_claude-opus_e3_unknown_entity_type_gate_design_2026-08-10.md)  
- [REVIEW_codex-sol_e3_unknown_entity_type_gate_design_2026-08-10.md](../../design/reviews/REVIEW_codex-sol_e3_unknown_entity_type_gate_design_2026-08-10.md)  
**Amends:** E3 gates in [e2_e3_claims_relations_design.md](e2_e3_claims_relations_design.md)  
**Ontology:** D18 closed entity vocabulary (`CORE_MANIFEST` + packs)

## 1. Decision

When the E3 normalizer emits an entity type **not** in the deployment’s
`entity_types` registry:

1. **Do not mint** under that type.
2. **Do not coerce** to `Concept` (or any other type).
3. **Inner-retry** the normalizer LLM call for that claim (budget below) with a
   stricter type reminder; use a **new cost `call_key`** each attempt.
4. After budget: **drop** every relation and observation assertion that still
   references an illegal type; log + metrics; **continue** the version job.
5. **Track rates** (events, retries, recovered, dropped, top labels).
6. Version the behavior with a new **`E3_NORMALIZER_VERSION`** string.

A document-version `normalize_relations` job **must succeed** when only soft
failures remain (unknown type after budget, unknown predicate, signature
reject). It must **not** dead-letter solely because the LLM invented a type.

## 2. Incident (why this exists)

BEAM 1M on `umc-beam-bench-01`: Claimify finished (~15k claims); E3 dead-lettered on:

```text
ForeignKeyViolation: entities.type = 'Process' not in entity_types
```

Observations stayed 0; later stages and scoring never ran. Migrations and core
seed were healthy (eight core types; no `Process`).

### Which path actually hits the FK

| Path | Pre-resolve gate today | Mint risk |
| --- | --- | --- |
| **Relations** | `_signature_allows` uses `type_parents`; unknown types typically **signature-reject** before resolve | Low for pure unknown types |
| **Observations** | **No type gate** — resolve/mint immediately | **Primary FK path** (incident class) |

Defense-in-depth still gates types on **both** relation and observation
endpoints before any resolve call, so neither path can mint illegal types.

## 3. Real mint path (implementation contract)

E3 does **not** call `EntityRegistry.resolve_t0` for production minting.

Normalize uses **`CascadeResolver.resolve`** (via the resolver wired into E3),
which mints through its cascade mint path (`_INSERT_ENTITY`).

| Layer | Responsibility |
| --- | --- |
| `E3Normalizer._normalize_claim` | Detect illegal types on `NormalizationResponse`; inner retry; filter illegal-bearing relations/observations **before** resolve; unique `call_key`s |
| `CascadeResolver` mint | Refuse mint if type ∉ registry (typed error); never insert illegal type |
| Work ledger | Unchanged outer attempts for systemic failures |

## 4. Allowed types

```text
allowed_types = keys of entity_type_parents(deployment)  # same set as prompt
```

Match is **exact** string equality with registry type keys (no fuzzy match).

## 5. Per-claim algorithm

```
allowed = load_allowed_types(deployment)  # once per version job is fine
MAX_INNER = 2  # first generate + one retry

response = generate(prompt, call_key=f"normalize:{claim_id}:a1")
for attempt in 1..MAX_INNER:
    illegal = collect_types(response) - allowed
    if not illegal:
        if attempt > 1: metric recovered
        break
    metric unknown_type_event(illegal, attempt)
    if attempt == MAX_INNER:
        break
    response = generate(
        prompt + retry_suffix(illegal, allowed),
        call_key=f"normalize:{claim_id}:a{attempt+1}",
    )
    metric unknown_type_retry

# Full replacement: use the final `response` only (not merge with earlier).
for each relation in response.relations:
    if any endpoint type illegal: drop; metric dropped(kind=relation); continue
    # existing: other-predicate funnel, unknown predicate drop, signature, resolve, upsert
for each observation in response.observations:
    if subject type illegal: drop; metric dropped(kind=observation); continue
    # existing resolve + batch observations
```

### Retry semantics

- **Temperature 0** is required so the **only** intended source of change is the
  **prompt suffix** (list illegal tokens + closed allowed list).
- **Full response replacement:** the last successful generate replaces the
  prior response entirely (legal facts from attempt 1 may be omitted — accepted
  tradeoff; metrics quantify recovered/drop).
- **Soft vs systemic on generate raise:** only
  `ProviderInvalidResponseError` (structured-output content poison) is
  claim-soft — log `e3.claim_normalize_error`, skip that claim, **continue**
  the version job (including when every claim soft-fails with zero facts;
  emit `e3.normalize_all_soft_failed`). Generic `ProviderCallError`,
  transport/timeouts, database errors, `UnregisteredEntityTypeError`, and
  unexpected bugs **re-raise** so the outer work ledger retries or dead-letters.
  Soft isolation must not convert a true outage into a successful empty
  normalize; the soft class is intentionally narrow (same pattern as E1
  chunk poison).

### Cost ledger keys

`WorkLedger.record_call` is unique per `(processing_id, attempt, call_key)`.

| Call | `call_key` |
| --- | --- |
| First normalize | `normalize:{claim_id}:a1` |
| Retry | `normalize:{claim_id}:a2` |
| Soft invalid-response failure | `normalize:{claim_id}:aN:failure` (only when soft-isolated; systemic failures use Worker `provider_failure`) |
| Resolve calls | keep existing distinct keys |

Never reuse `normalize:{claim_id}` alone across inner attempts (silent unbilled
retries). Do not double-bill a systemic `ProviderCallError` under both
`aN:failure` and `provider_failure`.

## 6. Isolation (document blast radius)

`_normalize_claim` (or its caller loop) must ensure:

- Soft drops (unknown type after budget, unknown predicate, signature reject)
  never raise.
- **Claim-soft exceptions** are only `ProviderInvalidResponseError`; log
  `e3.claim_normalize_error` with `error_class` and continue the claim loop.
- **Systemic exceptions** (other provider errors, DB, mint refusal, bugs)
  re-raise immediately — do not mark the version succeeded empty.
- After all claims without systemic abort: always return terminal follow-ups
  (adjudicate + embed_claim). Log `e3.claims_processed` once per job.

This is what makes “one bad type cannot dead-letter the doc” true for the type
gate and for single-claim content poison, without hiding outages.

## 7. Component versioning and DLQ recovery

- Bump `E3_NORMALIZER_VERSION` (e.g. suffix `:unknown-type-gate-1`).
- New normalize work uses the new component version string in the ledger.
- **Replay** of an old dead-lettered row at the old component version: operator
  replays after deploy; if the same `component_version` is reused, behavior
  change would be provenance-wrong — **bump version** so old vs new is
  auditable. Replay instructions: after deploy, `remember ops replay` the
  dead-lettered `normalize_relations` processing row (or re-enqueue at new
  version if conflict policy requires).

## 8. Metrics

Emit structured logs (and counters if the worker metrics surface exists) with
**bounded** labels (do not put raw free-form model dumps in metric labels;
truncate type tokens to a short allowlist-or-other bucket if cardinality
explodes).

| Event | Required fields |
| --- | --- |
| `e3.unknown_entity_type` | `claim_id`, `attempt`, `illegal_types` (tuple), `site=relation\|observation\|response` |
| `e3.unknown_entity_type_retry` | `claim_id`, `attempt` |
| `e3.unknown_entity_type_recovered` | `claim_id`, `attempts_used` |
| `e3.unknown_entity_type_dropped` | `claim_id`, `illegal_types`, `kind=relation\|observation` |
| `e3.claim_normalize_error` | `claim_id`, `error_class` (unexpected) |

**Denominators** (for rate queries): `claims_processed` per version job (log once
per job with count), and per-call normalize generate count from cost ledger.

**FK alarm:** if `IntegrityError` on entity type still occurs, log
`e3.entity_type_fk_violation` at error — should be ~zero after this design.

## 9. Testing

| Case | Expect |
| --- | --- |
| Observation with type `Process`, legal second response | One retry; second applied; no exception; cost keys a1+a2 |
| Observation illegal twice | Dropped; no entity mint with Process; job continues |
| Relation with illegal types | Dropped before resolve (even if signature would also reject) |
| Mixed legal + illegal in one final response | Legal kept; illegal dropped |
| All-legal | Single generate; no retry |
| Version with N claims, one always-illegal | Other N−1 still process; terminal branches still enqueued; job success |
| Resolver defense | Mint path rejects unregistered type if called |

## 10. Docs / product surface

Update ingestion pipeline docs that claim “never silent skip” (e.g. website
pipeline page) to say: **re-derivable soft drops** (unknown predicate, signature
reject, **unknown entity type after retry budget**) are intentional and
instrumented; they are not silent in metrics/logs.

## 11. Alternatives not chosen

| Alternative | Why not |
| --- | --- |
| Coerce to Concept | Silent durable type rewrite; binds lemma type forever |
| Auto-insert `entity_types` | D18 pollution |
| Status quo FK + job DLQ | Document blast radius; blocks scoring |
| Per-claim work-ledger fan-out | Larger change; defer |

## 12. Implementation checklist

1. Dual design review fixes absorbed in this revision (D86, CascadeResolver,
   cost keys, observation-first narrative, isolation, versioning, tests).
2. Implement + unit tests.
3. Dual impl review.
4. Replay BEAM host normalize DLQ; confirm observations and later stages.
