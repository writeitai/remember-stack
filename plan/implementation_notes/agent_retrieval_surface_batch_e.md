# Agent retrieval surface — Batch E implementation note

> **Historical implementation note.** D87 removes `question_context`; the
> candidate-tail mechanics below remain implementation history, not a current
> public descriptor. `open_query_space_design.md` §3.1 is binding.

**Date:** 2026-08-03
**Binding design:** [`agent_retrieval_surface_design.md` §3.4](../designs/agent_retrieval_surface_design.md)

## Affected hybrid paths

The registry contains three recipes with the `candidate_k` nominate/fuse/final-`k` pattern:
`claims_hybrid_rrf`, `chunks_hybrid_rrf`, and the two-grain `question_context`. The Batch D
`multi_hop_context` compound operation also embeds the same question-context mechanics with fixed
200-candidate and 50-result bounds, so it shares the implementation and receives the corresponding
recipe-version roll. Batch B and C recipes do not use this path and are unchanged.

Each semantic and BM25 channel is still read exactly once. RRF now retains the complete deterministic
union of those already-fetched nominations. PostgreSQL confirmation runs over that pool, preserving
RRF order, and the final `k` cut happens afterward. A candidate rejected by confirmation therefore
exposes the next confirmed item from the existing tail; there is no second index search, changed-state
window, or re-nomination round. `dropped_by_hydration` counts every candidate in the confirmed pool
that failed the authoritative claim or live-chunk checks, including tombstoned document lineages.

## Exact-text grouping and confirmation

Claim hybrids group the confirmed pool before the final cut. The normalizer performs only the bound
sequence: Unicode NFKC compatibility composition, Unicode `casefold`, whitespace-run collapse to one
space, then removal of leading and trailing Unicode punctuation. It does not stem, lemmatize, edit
internal punctuation, or compare embeddings. The normalizer has no independent version key; the
affected recipe versions own its behavior.

The first confirmed claim in RRF order represents a group. Its optional `EvidenceResult` additions
are:

- `corroboration_count`: the number of distinct `doc_id` document lineages among confirmed members;
  repeated claims from one document count once; and
- `grouped_claim_ids`: every confirmed member claim ID in original ranking order, including the
  representative.

Grouping operates only on PostgreSQL-confirmed `EvidenceResult` records. A non-current or tombstoned
candidate can increment `dropped_by_hydration`, but can never appear in `grouped_claim_ids` or affect
the corroboration count. The fields default to `None` and `()` respectively, so envelopes stored
before Batch E continue to validate. Chunk evidence is refilled but is not claim-grouped: the design's
group-member contract is explicitly claim-ID-shaped.

## Recipe and protocol identity

The behavior-bearing recipe versions are now `claims_hybrid_rrf` v6, `chunks_hybrid_rrf` v3,
`question_context` v3, and `multi_hop_context` v2. Tool descriptors now expose the integer recipe
version (optional on the client model for compatibility with older servers). Descriptor disclosures
name tail refill and, wherever `EvidenceResult` carries it, exact-text grouping and distinct-lineage
corroboration. The catalog SHA-256 and both locked full-v9 fingerprints rolled; the protocol keys and
names remain `full-v9` and `full-v9-strong` as required.

## Envelope bounds

Record-count bounds do not increase: claim/source hybrids still return at most their public `k`, and
`multi_hop_context` still applies its hard 60-record claim/chunk budget. Grouping adds scalar/reference
metadata without duplicating claim text:

| Path | Returned text records | Worst-case grouped-ID references |
| --- | ---: | ---: |
| `claims_hybrid_rrf` | 100 claims | 800 IDs (two 400-candidate channel lists) |
| `question_context` | 100 claims + 100 chunks | 800 IDs on its claim half |
| embedded `multi_hop_context` question claims | within the existing 60-record content budget | 400 IDs (two fixed 200-candidate lists) |
| `chunks_hybrid_rrf` | 100 chunks | none |

The union bounds assume no cross-channel candidate overlap; overlap only reduces them. Every returned
group also adds one nullable integer. Existing text fields and JSON arrays have no byte-length maximum,
so there is no honest finite serialized-byte ceiling. The largest Batch E growth is therefore 800 UUID
references plus at most 100 corroboration integers, with no additional claim-text payload.

## Verification

The required gates ran in the specified order:

1. DB-backed Batch B–E, benchmark, registry, envelope, and migration suite: **174 passed, 0
   skipped**. The reusable disposable database initially contained an unrelated, dependency-free
   `btree_gin` extension; it was removed because the repository schema contract rejects undeclared
   extensions, then the complete command was rerun from the beginning and passed.
2. `uv run pyright src/ benchmarks/`: **0 errors, 0 warnings**.
3. `uv run ruff check src/ benchmarks/`: **passed**.
4. `.venv/bin/python -m ruff format --check src/ benchmarks/`: **299 files already formatted**.

As an additional integration check before the ordered gate, the complete `src/tests/surfaces/`
suite passed **171 tests** and the benchmark suite passed **78 tests**.
