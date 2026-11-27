# Response to D162 rescope review, round 4

PR #681 at reviewed head `52466e912`. Accepted both P1 findings, the P2
implementation follow-up and the citation nit. No P0 findings.

## P1

- **Exact benchmark binding.** An offline probe changed the embedding
  gateway URL and then the zero-data-retention flag while keeping the model
  and provider fixed. Each change altered the request and passed the
  current LoCoMo binding guard. BEAM's answer path starts retrieval at the
  supplied API URL. D162 requires the exact worker, reader, schema and
  passage and query embedding fingerprint at protected ingest, replay and
  reads. Missing or mismatched identity keeps the pinned path active. The
  release check covers gateway and retention changes with the same model
  and provider.
  [LoCoMo guard](../../benchmarks/locomo/runner.py#L1714-L1729) ·
  [Binding report](../../src/rememberstack/profiles/selfhost.py#L2373-L2399) ·
  [Embedding adapter](../../src/rememberstack/adapters/openrouter.py#L1001-L1046) ·
  [BEAM reader](../../benchmarks/rs_harness_beam/answer_agent.py#L692-L705) ·
  [D162 §5](../../plan/designs/split_oversized_chunks_design.md#5-benchmark-admission-and-release-checks).
- **Text-origin matches after publication.** E1 records dates at CHUNK and
  the current donor lookup reads chunk rows. A later piece can span
  multiple narrower recovery pieces and still match the original parent.
  When recovery replaces a grid, D162 retains the original spans, consumed
  context and matching metadata for D140 through publication. The release
  check packs February before January recovery and after January publication
  with different ceilings and requires the same inherited date. D159
  excludes hidden donors immediately and erases their retained records.
  [E1 origin write](../../src/rememberstack/workers/e1.py#L167-L216) ·
  [Donor lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L345) ·
  [D140 §5](../../plan/designs/effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time) ·
  [D159 sweep](../../plan/designs/archive_and_forget_design.md#33-the-sweep).

## P2

- **E1 acceptance check.** The YAML still requires semchunk, whole-block
  cuts and oversized atomic chunks. The D162 release check requires the
  implementation change to update it for lossless, non-overlapping,
  section-bounded splits and byte-identical fitting grids. The YAML edit
  belongs with implementation under this PR's Markdown-only scope.
  [Current check](../../plan/implementation_evals/eval_checks/e1_chunks_whole_blocks_no_overlap.yaml#L13-L22) ·
  [D162 §5](../../plan/designs/split_oversized_chunks_design.md#5-benchmark-admission-and-release-checks).

## Citation

The Full-v47 paragraph cites the runner for the frozen P3 inventory and
the registry for executable protocol selection.
[P3 attestation](../../benchmarks/locomo/runner.py#L709-L740) ·
[Protocol registry](../../benchmarks/locomo/protocol.py#L513-L553).

## Verification

414 focused existing tests passed across OpenRouter, the LoCoMo protocol
and runner, the BEAM reader, the chunker and D140 origin time. The gateway
and retention probe used the production adapter with a mock HTTP transport.
The new regression cases are specified as implementation release checks.
