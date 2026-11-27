# PR #681 response to rounds 1–4

Dead letters stay terminal. Round 1's stale lease, replay budget and repeat
rejection findings, round 2's D140 inheritance gap, and round 4's stranded
lineage all depended on replacing the stored grid. That operation is gone.
An edited successor uses normal version-scoped work. D159 permits a deliberate
re-add after forget. D140 still reads matching dates from saved failed-version
chunks. PostgreSQL 18 verified this in living and snapshot lineages. E0 can
already expose chunk handles and partial vectors before pipeline failure.
[Source audit](../../plan/analysis/split_oversized_chunks_analysis.md#existing-version-behavior).

Round 3's mixed-release finding stands. A large HTML gap can require a piece
with no E0 block, so legacy Selection misses its label beside a block-backed
neighbour. The D160 gate covers the affected workers and section readers.
The new handlers label each gap-only window by its exact span. Section entry
handles use the first block's start. A shared qualified binding and D80's
existing maintenance cutover cover model consistency.
[Split and rollout](../../plan/designs/split_oversized_chunks_design.md#2-split-only-failing-candidates) ·
[Legacy section lookup](../../src/rememberstack/spine/section_history.py#L737-L751).

A distinct split content hash prevents an equal-key whole-chunk donor and
retains D56 carry-forward for matching split inputs. The proposed pre-cap
donor comparison is gone. Legacy equal-input Selection rematerialization
and original Claimify donor provenance (rounds 1–2), and re-grounding reused
claims after a typed location changes (round 4), are separate follow-ups.
[Claimify reuse](../../src/rememberstack/workers/e2.py#L817-L859).

Whitespace-only labels preserve bytes without producing blank pieces.
Unusable vectors take bounded retries. Only `empty_body` can skip. The
offline gate compares full rows and ordered benchmark requests as sent.
Orchestration needed a narrow amendment because it allowed poison skips.
[Failure and acceptance](../../plan/designs/split_oversized_chunks_design.md#3-terminal-failure-and-the-next-version).
