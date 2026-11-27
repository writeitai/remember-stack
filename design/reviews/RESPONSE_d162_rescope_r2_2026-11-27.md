# Response to D162 rescope review, round 2

PR #681 at reviewed head `30bf6f821`. Accepted all four P1 findings and the P2
finding.

## P1

- **Pending passages.** Both pending queries test `NOT selectable`, which
  reflects version and representation readiness from E0. The empty-result
  helper can then report `known_empty` on exhaustion. D140 now specifies
  checking the selected grid's E1 publication in both pending queries inside
  the ranked-read snapshot. An empty search discloses a touched unpublished
  grid through `boundary` and `scope_pending`, including on a dead letter.
  The release check also covers a published empty grid and concurrent publication.
  [Readiness predicate](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L255-L267) ·
  [Candidate probe](../../src/rememberstack/surfaces/query_engine.py#L6834-L6850) ·
  [Touched check](../../src/rememberstack/surfaces/query_engine.py#L6710-L6724) ·
  [Empty result](../../src/rememberstack/surfaces/query_engine.py#L5303-L5320) ·
  [D140 §3.7](../../plan/designs/effective_time_and_section_references_design.md#37-results-carry-their-period-and-pending-lineages).
- **Gap-only target passages.** A neighbour's clipped block suppresses the
  global chunk-span fallback, leaving Selection without the target label that
  D122 requires. Label each eligible gap-only window by its exact piece span
  with clipped provenance. The release check publishes a card from an HTML gap
  beside a neighbour with a real block.
  [Catalogue](../../src/rememberstack/core/source_passages.py#L124-L158) ·
  [Target requirement](../../src/rememberstack/core/selection_references.py#L153-L158) ·
  [D162 §2](../../plan/designs/split_oversized_chunks_design.md#2-coordinates-and-reuse).
- **Claimify citation reuse.** E2's local query finds the closest same-owner
  neighbours, while Claimify reuse translates only `target-9` through
  `target+1`. A secondary citation beyond a child can therefore fail to
  remap with identical inputs. Use the paired local slots and the verified
  supports of admitted cards. The release check covers both layout directions
  and requires exact remapped spans, the same claim IDs and zero Claimify calls
  for matching targets. The preceding-eight check includes empty producers.
  [Local slots](../../src/rememberstack/spine/chunk_catalog.py#L373-L419) ·
  [Admitted supports](../../src/rememberstack/workers/e2.py#L911-L930) ·
  [Ordinal remap](../../src/rememberstack/workers/extraction_references.py#L31-L53) ·
  [D162 §2](../../plan/designs/split_oversized_chunks_design.md#2-coordinates-and-reuse).
- **Erasure of staged content.** The sweep deletes the current chunk and P1
  rows using document and collected chunk IDs. Add D162 staging to the sweep
  by document ownership, covering all deliveries even after their chunk rows
  are gone. Stage and publish under the lineage lock used by the hide so a
  late reply cannot recreate content. The release check forgets a partial
  batch and releases an obsolete reply after the hide.
  [Hide lock and tombstone](../../src/rememberstack/workers/reconcile.py#L1116-L1136) ·
  [Chunk deletes](../../src/rememberstack/spine/forget.py#L1934-L1939) ·
  [D159 §3.3](../../plan/designs/archive_and_forget_design.md#33-the-sweep).

## P2

- **Legacy commit order.** E1 calls P1 first and stamps chunk authority in a
  separate transaction. Both the orchestration and P1 designs state that
  order. It explains why D162 recovery can encounter a stored P1 vector with
  a missing authority stamp.
  [Worker order](../../src/rememberstack/workers/e1.py#L569-L572) ·
  [P1 transaction](../../src/rememberstack/adapters/postgres_p1.py#L283-L299) ·
  [Authority transaction](../../src/rememberstack/spine/chunk_catalog.py#L252-L258) ·
  [Orchestration](../../plan/designs/orchestration_design.md#embed_chunk-durability-d80--minimum-contract).

## Verification

Provider-free probes reproduced the missing HTML gap label and the failed
cross-child citation remap in both directions. All 56 focused existing tests
passed. [D162 §5](../../plan/designs/split_oversized_chunks_design.md#5-offline-benchmark-and-release-checks)
lists the implementation release checks.
