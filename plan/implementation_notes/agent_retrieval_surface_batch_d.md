# Agent retrieval surface — Batch D implementation note

**Date:** 2026-08-03
**Binding design:** [`agent_retrieval_surface_design.md` §3.3](../designs/agent_retrieval_surface_design.md)

## Compound operation and graph confirmation

`multi_hop_context` is one recipe step over one compound `query_engine` method. The executor
passes its composed P2 surface into that method; it does not attempt to thread graph envelopes
through a multi-step recipe chain. The recipe linter and executor each register the compound op as
EVIDENCE grain. This is the recorded linter rule change for Batch D; the ordinary chain dataflow and
`combine_evidence` type guard are unchanged.

Both entity strings use the Batch B T0 resolution helper in parameter order. A missing entity returns
`unknown_entity`; ambiguity returns every ranked `EntityCandidate` in `entities[]` plus a `boundary`
negative. After resolution, the two-entity form asks P2 for a shortest path and the one-entity form
asks for the distance/name/id-ranked neighborhood. The neighborhood's internal edge-carrying mode
uses those same ranked shortest paths, deduplicates relation IDs in rank order, and stops at `k` edges;
the existing public `graph_neighborhood` recipe retains its prior node-only shape.

P2 only nominates structure. One PostgreSQL statement accepts the complete ordered relation-ID array
for all returned edges and, in one repeatable-read round:

- confirms that every relation still belongs to the deployment, is believed, and is currently valid;
- replaces projected edge/entity labels and temporal fields with live PostgreSQL values;
- detects open `support_withdrawn` review flags;
- joins both evidence stances; and
- applies the Batch C current-testimony, cached-lineage agreement, and fail-closed tombstoned-lineage
  filters before source-diverse rank and exact count windows.

There is no edge-by-edge SQL loop. A normal edge without current supporting testimony is dropped; an
edge with an open D54 processing-withdrawal flag is the sole exception and stays with
`support=withdrawn`, even when both exact evidence totals are zero. A path with any dropped edge is
dropped as a unit under D48. When resolved endpoints have no confirmed path within the hop bound, the
result is `known_empty`, not `unknown_entity` or `boundary`. If an existing path itself cannot fit an
explicitly smaller `k` edge cap, the result is instead an honest `boundary` instructing the caller to
raise `k`; it never misreports that capacity boundary as an empty graph.

## Evidence selection and question context

Each retained edge gets exact `supports` and `contradicts` totals. Selection is rank-round-robin over
edges and stances, reusing Batch C's allocator: all edges see rank zero before any edge receives rank
one. `fact_evidence[]` names every selected edge/claim/stance association explicitly, while
top-level `evidence[]` deduplicates claim UUIDs. The ordinary question-context mechanics then run
inside the compound method: semantic and BM25 claims plus semantic and BM25 chunks, 200 nominations
per channel, RRF, at most 50 confirmed claims and 50 confirmed chunks before the final union. Final
`evidence[]` and `chunks[]` are each ID-deduplicated.

The hard content budget is 60 returned claim/chunk records. Edge evidence is allocated first so the
round-zero guarantee cannot be displaced by general question context; deduplicated question claims
and then chunks fill only the remaining capacity. When this cap or the graph edge cap elides records,
the envelope's aggregate truncation block counts returned versus pre-union edge/content records. Per-
edge `evidence_totals[]` remain the exact disclosure for stance depth.

## Envelope bounds

At the public maxima (`k=30`, `hops=2`, `evidence_per_fact=5`), uncapped edge evidence would be:

`30 edges × 5 claims × 2 stances = 300 edge/claim associations`.

The 60-association cap covers the complete rank-zero round because
`30 edges × 1 claim × 2 stances = 60`. Thus every ordinary retained edge exposes its first supporting
claim, and every edge with contradicting testimony exposes that stance before any second claim is
selected. A D54-withdrawn edge may honestly have no current claim and reports zeros instead.

| Payload | Maximum records |
| --- | ---: |
| top-level `edges[]` | 30 |
| top-level `paths[]` | 30 neighborhood paths (one path in the two-entity form) |
| top-level `nodes[]` | 60 distinct edge endpoints |
| top-level `evidence[]` + `chunks[]` | 60 total content records |
| `fact_evidence[]` | 60 associations |
| `evidence_totals[]` | 30 × 2 = 60 exact per-stance totals |
| nested path members | 30 × (3 nodes + 2 edge copies) = 150 |

That is at most 300 top-level list records and 150 nested path-member records, or 450 list records
including structural copies. The text-bearing term is hard-bounded at `60 × hydrated claim/chunk
payload`; selected association rows do not duplicate claim text. Existing text fields have no byte-
length maximum, so there is no honest finite serialized-byte ceiling. Byte caps and continuation
tokens remain the design's recorded §6 deferral.
