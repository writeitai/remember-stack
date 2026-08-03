# Agent retrieval surface — Batch B implementation note

**Date:** 2026-08-03
**Binding design:** [`agent_retrieval_surface_design.md` §3.1 and §4](../designs/agent_retrieval_surface_design.md)

## Authoritative join and index plan

`documents_about` reads `resolution_decisions → mentions → documents`, restricted to the current
unsuperseded resolution decision and a live document. It left-joins the current version and
representation only to obtain `markdown_uri`, groups by document, and orders by distinct mention
count, latest mention time, then document UUID. PostgreSQL remains authoritative throughout.

The join uses:

- new partial `ix_resdec_entity_live (deployment_id, entity_id, mention_id) WHERE superseded_by IS
  NULL` to identify live decisions and cover the mention-id handoff;
- the partition-local `mentions` primary key for the mention lookup and `documents_pkey` for the
  document lookup; and
- existing current-version/representation unique indexes for optional artifact metadata.

The `k` bound limits the returned envelope, while the authoritative aggregation still visits every
matching live mention needed to calculate ordering and the exact result total. That distinction is
material for hub entities: no hidden pre-aggregation or approximate count was introduced.

`claims_about` extends the same live-resolution path through `chunk_claims → claims`. Without a
question, PostgreSQL returns the first `k` by assertion/ingestion recency. With a question it returns
at most 400 entity-filtered claim IDs, the query is embedded once, P1 supplies vectors only for those
IDs, and the top `k` are D48-confirmed. It never performs a global vector nomination followed by an
entity filter.

`claims_as_of` uses the new partial
`ix_claims_valid_window (deployment_id, claim_valid_from, claim_valid_until) WHERE
claim_valid_precision <> 'unknown'`. PostgreSQL performs inclusive interval intersection and returns
at most 400 stamped IDs for an optional bounded semantic rerank. The exact unstamped count is read in
the same repeatable-read snapshot.

For both semantic forms, the ranking pool is the deterministic PostgreSQL head, not every match in
the envelope's exact `total`: `claims_about` ranks at most 400 live entity-filtered claims ordered by
assertion time, ingestion time, then claim UUID; `claims_as_of` ranks at most 400 live stamped claims
ordered by most-recent `claim_valid_from`, then claim UUID. This is deliberately filter-in-PostgreSQL,
rank-the-bounded-set: filtering before ranking avoids the recall ceiling of global semantic nomination,
while design principle 7 bounds projection work and the returned envelope.

## Measured plans

Measurements used PostgreSQL 16.14 in the repository's pinned pg_partman image, `work_mem=4MB`, two
parallel workers, and a warm local Docker cache. They are reproducible plan checks, not hosted latency
claims or p95 measurements.

- `documents_about`, 100,000-document/100,000-mention single-entity hub, `k=20`: PostgreSQL chose
  parallel sequential scans for the 100%-selective hub, a parallel hash join, `GroupAggregate`, exact
  `WindowAgg`, and a top-N heapsort. Planning was 7.183 ms and execution 160.718 ms. The aggregation
  spilled 1,001 temporary pages at the configured 4 MB `work_mem`. This is below the design's 250 ms
  P1-adoption trigger as a single measured execution, but is not a p95 claim.
- `documents_about`, 100 matching mentions inside the same 100,100-decision store: the live-resolution
  partial index was an index-only scan; mention and document joins were indexed nested loops. The full
  recipe planned in 7.847 ms and executed in 1.930 ms. The isolated live-resolution lookup executed in
  0.136 ms.
- `claims_as_of`, 90,000 claims with exactly one third stamped, June 2024 window, `k=20`: the partial
  validity index was scanned backward on the populated partition, produced 2,470 intersecting claims,
  then an exact `WindowAgg` and incremental top-N sort. Planning was 5.519 ms and execution 6.711 ms.

## Envelope bounds

The hard evidence budget is respected by construction:

| Recipe | Maximum returned records | Serialized-size bound |
| --- | ---: | --- |
| `documents_about` | 50 `SourceRecord` values | 50 × source metadata/title/URI payload |
| `claims_about` | 50 `EvidenceResult` values | 50 × hydrated claim/source-span payload |
| `claims_as_of` | 50 `EvidenceResult` values | 50 × hydrated claim/source-span payload |
| `chunk_neighbors` | 5 `ChunkEvidenceResult` values | 5 × hydrated chunk/context payload |

Every recipe is at or below the design's 60-record hard evidence budget. Text fields in the existing
envelope schema have no byte-length maximum, so there is deliberately no fictitious finite serialized
byte ceiling; the formulas above are the honest worst-case envelope statement. Any future byte cap or
continuation token is the recorded §6 deferral, not a Batch B contract change.
