# Response to PR #681 simplified design review, round 4

Review of `15325ae2d`. Accepted the P1, both P2 findings and the packing nit.

## P1

- **Shared embedding limits.** Self-host uses the shared binding for queries,
  claim and fact writers, entity resolution and profile refresh. A mock
  eight-item cap accepted a singleton query and rejected nine-item requests
  from both P1 handlers. All three requests succeeded with a cap of 64. D80
  requires each shared consumer's existing requests to fit and retains the
  outgoing binding on incompatibility or unknown fit. E1-local changes use
  passage fit and batch grouping. D162 acceptance covers both refusal and
  compatible publication.
  [Query wiring](../../src/rememberstack/profiles/selfhost.py#L1424-L1449) ·
  [P1 writer wiring](../../src/rememberstack/profiles/selfhost.py#L1953-L1978) ·
  [Claim requests](../../src/rememberstack/workers/p1.py#L114-L123) ·
  [Fact requests](../../src/rememberstack/workers/p1.py#L358-L367) ·
  [D80 cutover](../../plan/designs/e1_embedding_input_policy.md#45-migration-triggers-and-vector-reuse).

## P2

- **Ineligible Selection producers.** E1 schedules Selection for every chunk
  in the extraction fan-out. E2 freezes zero propositions and cards without
  a model call for each ineligible chunk. E1 and D133 now specify both steps
  for split pieces. The frozen empty result counts toward the D122 barrier
  and keeps its slot in the producer window.
  [Fan-out](../../src/rememberstack/workers/e1.py#L854-L873) ·
  [Empty result](../../src/rememberstack/workers/e2.py#L537-L572) ·
  [Barrier predicate](../../src/rememberstack/spine/work_ledger.py#L2685-L2698) ·
  [Producer window](../../src/rememberstack/workers/extraction_references.py#L56-L76).
- **Rule 4.** The working agreement links D162's version-wide exception for
  vectors, Selection, Claimify and D140 text-origin dates. It keeps the
  edit-locality requirement and its test for eligible version pairs.
  [Rule 4](../../CLAUDE.md#L74) ·
  [D162 reuse gate](../../plan/designs/split_oversized_chunks_design.md#3-version-wide-reuse-gate).

## Nit

- **Packing realignment.** A packer probe changed which intermediate anchor
  cleared the minimum gap by lengthening one block. Both versions realigned
  at the next common anchor. The design describes that boundary and the end
  of the owner run.
  [Anchor acceptance](../../src/rememberstack/core/chunker.py#L221-L239) ·
  [Packing design](../../plan/designs/e1_chunks_design.md#4-non-overlapping-chunks-within-sections-d58-d162).

The baseline passed 62 unit tests and 31 PostgreSQL 18 tests, with one
expected D122 locality failure. Git whitespace and 24 added relative links
passed. Hosted qualification and frozen-checkpoint equality remain release
checks.
