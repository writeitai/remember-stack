# E3 unknown entity type vs closed ontology — analysis

**Status:** analysis (non-binding)  
**Date:** 2026-08-10  
**Incident:** BEAM 1M conversation-1 on `umc-beam-bench-01` — `normalize_relations` dead-lettered after Claimify completed (~15k claims).  
**Related binding design:** [e3_unknown_entity_type_gate_design.md](../designs/e3_unknown_entity_type_gate_design.md) (D86)

## 1. Problem frame

Plane E3 turns claims into relations and observations. Entity identity goes through
T0 resolution (`EntityRegistry.resolve_t0`), which **mints** a row in `entities`
when the lemma is new. That insert requires `(deployment_id, type)` to exist in
`entity_types` (FK).

The normalizer LLM is prompted with the **closed** type list for the deployment
(core eight + extension packs). In production it still occasionally emits types
outside that set (example: `Process` for “caching process”).

When that happens today:

1. Predicate / signature gates may or may not drop the triple first.
2. If resolution reaches mint with the illegal type, Postgres raises
   `ForeignKeyViolation`.
3. The exception escapes the **document-version** E3 job.
4. Work-ledger retries the whole version job, then **dead-letters**.
5. Downstream stages (claim embed, supersession, labels) never start; observations
   stay empty; benchmarks cannot score.

This is **not** a migration failure: bootstrap seeds the core vocabulary correctly.
It is a **control-plane gap**: closed ontology + open LLM + fail-the-job.

## 2. Evidence (BEAM 1M)

| Fact | Observation |
| --- | --- |
| Alembic | `p9_07_0028` (healthy) |
| Seeded types | Person, Organization, Place, Document, Event, Concept, Project, Product — **no Process** |
| Extract | 1793/1793 chunk jobs succeeded; ~15 000 claims |
| Normalize | `dead_letter` after 3 attempts |
| Error | `entities_deployment_id_type_fkey` — type `Process` missing from `entity_types` |
| Relations | ~118 partial (work committed before crash) |
| Observations | 0 (E3 observation path never completed successfully) |
| Scoring | not run |

## 3. Existing soft gates (what already works)

In `E3Normalizer._normalize_claim` today:

- Unknown **predicate** → log + **continue** (“re-derivable”).
- Signature reject (LLM types or resolved types) → log + **continue**.
- `other:*` predicates → `ensure_other_predicate` (explicit funnel).

There is **no** parallel soft gate for unknown **entity types** before mint.
The FK is the only hard stop — and it fails the **entire** version job, not one claim.

## 4. Alternatives

| Option | Pros | Cons |
| --- | --- | --- |
| **A. Auto-register LLM types** | Never FK | Pollutes D18 ontology; predicates/signatures explode; irreversible mess |
| **B. Coerce unknown → Concept** on first sight | Simple; keeps recall | Loses type signal; first-try flukes become permanent Concept |
| **C. Drop assertion on first unknown type** | Clean graph | Throws away recoverable LLM errors without retry |
| **D. Retry LLM, then drop** (preferred) | Recovers flukes; preserves closed ontology; no silent coerce | Extra cost on rare path; needs metrics |
| **E. Fail whole job (status quo)** | Forces investigation | Destroys pipeline progress; zero observations; no scoring |
| **F. Fan-out normalize per claim** | Retry granularity like D84 | Large design; not required to fix this class |

**Rejected:** A (ontology discipline), E (incident mode), B as **first** response (user decision: no coerce; drop after budget).  
**Deferred:** F as a later scale proposal if version-level E3 remains too coarse for other errors.

## 5. Preferred policy (consensus for binding design)

1. **Detect illegal types before mint** (do not rely on FK as the primary gate).
2. On first (and optional second) failure for that claim: **re-call** the normalizer
   with an explicit reminder of the allowed type set — **do not** drop/coerce yet.
3. After the per-claim re-call budget: **drop** the offending relation or observation
   assertion (log + metrics). **Never coerce** to Concept under this decision.
4. **Continue** other claims in the same version job so one bad label cannot
   dead-letter the document.
5. **Track rates**: unknown-type events, recovered-on-retry, dropped-after-budget,
   top illegal labels, rate per claim and per normalize LLM call.
6. Keep FK as a **last-resort safety net**; if it still fires, treat as unexpected
   defect (metrics + fail that claim path / re-raise only if systemic).

## 6. Cost model

Unknown-type rate on BEAM extract-scale traffic is expected to be **≪1%** of claims
if the prompt lists types. At 15k claims:

- If 0.1% need one retry → ~15 extra normalize calls → cents at Flash pricing.
- Full job DLQ + human replay costs far more wall-clock and operator time.

Inner retries are therefore cheap relative to version-level dead-letter.

## 7. Interaction with work-ledger attempts

| Layer | Role |
| --- | --- |
| Inner re-call (1–2) | Soft recovery for unknown type on one claim |
| Work-ledger attempts on `normalize_relations` | Systemic failures (DB down, provider outage, code bugs) |

Inner retries **must not** consume the only path to completing a version after a
single vocabulary fluke.

## 8. Open implementation notes

- Where to gate: after `NormalizationResponse` parse, **before**
  `CascadeResolver.resolve` mint (E3 does not use `EntityRegistry.resolve_t0`
  for production resolve).
- Relation triples often already fail closed via `_signature_allows` when the
  type is absent from `type_parents`; **observations** had no type gate — the
  primary FK path in the BEAM incident.
- Inner retries need unique cost `call_key`s (`normalize:{claim_id}:aN`); reuse
  of `normalize:{claim_id}` silently drops retry spend from the ledger.
- Metrics: structured logs first; denominators = claims_processed + generate
  calls; bound label cardinality on illegal type strings.

## 9. Conclusion

The BEAM failure is an **E3 control-plane gap**, not a schema migration gap.
**Retry-then-drop** with **rate tracking** and **per-claim isolation** is the
smallest change that preserves ontology discipline and pipeline progress.
