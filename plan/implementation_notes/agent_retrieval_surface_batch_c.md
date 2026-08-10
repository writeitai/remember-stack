# Agent retrieval surface — Batch C implementation note

> **Historical implementation note.** D87 removes `current_context`; its
> replacement is the temporally complete `fact_context` contract in
> `open_query_space_design.md` §3.1. The mechanics below describe the built
> predecessor and are not an active tool contract.

**Date:** 2026-08-03
**Binding design:** [`agent_retrieval_surface_design.md` §3.2](../designs/agent_retrieval_surface_design.md)

## Nomination and confirmation

`current_context` is query-driven and has no entity-resolution stage. It embeds `query` once and
asks the existing P1 facts channel (`search_facts`, relations and observations together) for `k + 1`
IDs. The extra ID is only a truncation probe: at most `k` IDs enter PostgreSQL confirmation, while a
returned probe makes the envelope's fact-list truncation explicit without pretending the P1 port
reported an exact corpus-wide total.

The recipe-chain linter and executor each register `current_context` as one validity-filtered,
FACT-grain compound operation. The public recipe remains a one-step chain, so the existing chain
dataflow and linter rules are unchanged rather than special-cased.

PostgreSQL confirms the nominated IDs against both fact tables in nomination order. A relation or
observation must still be believed (`invalidated_at IS NULL`) and its valid-time interval must cover
the query instant. Confirmation and evidence hydration share one repeatable-read snapshot. The
existing fact enrichment then preserves the S23 contradiction block and D54
`support=current|withdrawn` marker.

## Evidence selection and totals

Evidence links are read from both `relation_evidence` and `observation_evidence`. A claim is eligible
only when all of the following hold:

- the evidence link, claim, and fact belong to the requested deployment;
- the link's cached document lineage agrees with the claim's lineage;
- `claims.is_current_testimony` is true; and
- an existing document lineage is live (`documents.deleted_at IS NULL`). Legacy/imported claims with
  no document catalog row retain the same compatibility posture as the Batch B confirmation path,
  while an existing tombstoned lineage fails closed.

The same current-testimony, deployment, lineage-agreement, and tombstone filters now tighten the
existing relation `explain` evidence/source SQL. Its public shape remains unchanged.

Within each fact and stance, selection is source-diverse: the newest claim from every distinct
document lineage ranks before a second claim from any lineage, with assertion time, ingestion time,
document UUID, and claim UUID providing deterministic order. PostgreSQL calculates the exact eligible
claim count before applying the `[1..5]` per-stance cap. The envelope therefore reports two
`evidence_totals` records for every returned fact, including zero-count stances.

The hard 60-association budget is allocated in rank rounds: every ranked fact receives the first
supporting and contradicting claim that exists before any fact receives a second claim of either
stance. Since `k <= 30`, the first round can consume at most 60 records; every returned fact therefore
has evidence even at the worst-case fan-out. Facts with no eligible evidence are counted in
`dropped_by_hydration` and are not returned. Top-level `evidence[]` is deduplicated by claim UUID;
`fact_evidence[]` preserves every selected fact/claim/stance association explicitly.

## Envelope bounds

At the public maxima (`k=30`, `evidence_per_fact=5`), `current_context` returns at most:

| Payload | Maximum records |
| --- | ---: |
| `facts[]` | 30 |
| nested contradiction co-members | 30 × 25 = 750 (the existing S23 cap) |
| `evidence[]` | 60 unique claims |
| `fact_evidence[]` | 60 associations |
| `evidence_totals[]` | 30 × 2 = 60 per-stance totals |

That is at most 960 list records including the existing nested contradiction members, plus one
contradiction block per affected fact and the ordinary envelope metadata. The evidence-text term is
hard-bounded at `60 × hydrated claim/source-span payload`, rather than the uncapped
`30 × 5 × 2 = 300` per-fact theoretical fan-out. Fact and co-member labels contribute at most
`(30 + 750) × label payload`. Existing text fields have no byte-length maximum, so there is no honest
finite serialized-byte ceiling; byte caps and continuation tokens remain the design's recorded §6
deferral.
