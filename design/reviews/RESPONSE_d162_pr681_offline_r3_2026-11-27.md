# Response to PR #681 offline grid review, round 3

Reviewed head `d89ac335b`. The review reported no P0 findings.

## P1

- **Offline parity.** Renumbering three chunks from `0, 1, 2` to
  `0, 10, 20` removed two D122 producers from the last chunk's window.
  Moving the D80 header and separator into the indexed body passed the
  embedding hash check and changed the `source_text` returned by body fetch.
  The design compares deterministic CHUNK and projection fields directly,
  including numeric ordinals, section ownership, the normalized body and
  the separate header. Both mutations must fail the same comparison.
  [D122 producer window](../../src/rememberstack/workers/extraction_references.py#L56-L63) ·
  [Body fetch](../../src/rememberstack/surfaces/query_sandbox/nomination.py#L811-L832) ·
  [Offline check](../../plan/designs/split_oversized_chunks_design.md#5-offline-benchmark-and-release-checks)
- **Whole-block and fragment vector reuse.** The source lookup requires the
  content hash and D80 hash as a pair. With the pinned Qwen tokenizer, the
  8,192-digit prefix has 8,193 tokens with the end token, and the next
  doubling probe has 16,385. The fitting block and split prefix render the
  same body-only D80 input. The design uses the existing D80 hash across
  the two content hash forms when the normal lookup yields no verified
  donor. It requires byte-identical E0 slices and the original
  delivery and mapping proofs. The release regression requires zero
  embedding calls for the unchanged prefix.
  [Carry-forward query](../../src/rememberstack/spine/chunk_catalog.py#L422-L439) ·
  [Source probe](../../plan/analysis/split_oversized_chunks_analysis.md#choice-and-scope) ·
  [Reuse design](../../plan/designs/split_oversized_chunks_design.md#2-coordinates-and-reuse)

## P2

- **Fixture evidence.** The implementation PR must identify the retained
  BEAM store by ingestion revision and snapshot ID, record the upstream
  data revision, fixture SHA-256 digests, and exact restore or extraction
  and check commands. It must publish the offline result and hosted
  qualification and update the E1 acceptance YAML to reflect D58 and D162.
  [Release checks](../../plan/designs/split_oversized_chunks_design.md#5-offline-benchmark-and-release-checks) ·
  [LoCoMo restore procedure](../../benchmarks/locomo/sharding/README.md#4-restore-one-conversation) ·
  [E1 acceptance YAML](../../plan/implementation_evals/eval_checks/e1_chunks_whole_blocks_no_overlap.yaml#L13-L23)
- **Rollout rehearsal.** The implementation release check exercises
  previous-release writes, ordinary SQL and active saved queries across
  schema expansion. It verifies D161 retry, the atomic manifest and
  saved-query transition, the passage gate and late-worker fencing. It
  rehearses rollback before enrollment on the expanded schema and rollback
  after enrollment using the verified pre-activation restore.
  [D160 and D161](../../plan/designs/packaging_distribution_design.md#61-migration-compatibility) ·
  [Manifest publication](../../src/rememberstack/surfaces/query_sandbox/saved_queries.py#L1846-L1883) ·
  [Rollout check](../../plan/designs/split_oversized_chunks_design.md#5-offline-benchmark-and-release-checks)

## Nit

The LoCoMo audit labels 352,993 as the number of possible block spans
enumerated for sizing.
[Fixture audit](../../plan/analysis/split_oversized_chunks_analysis.md#benchmark-fixture-audit)

## Verification

Provider-free probes reproduced the changed D122 window and body fetch and
confirmed identical D80 inputs under different content keys. All 28 focused
packer, D80 and D122 tests passed. Checked 22 local links and ran
`git diff --check`. The frozen stores, hosted binding and rollout require
implementation release evidence.
