# Response to PR #681 simplified design review, round 2

Review of `0a83a2367`. Accepted both P1 findings, both P2 findings and the nit.

## P1

- **Commit-time split gate.** Selection and Claimify look up donors, remap
  and publish separately. E1 also reads donors before writing its search row
  and authority stamp. D162 requires all four paths to check both versions
  through commit under the lineage lock. CHUNK resolves D140 origins and
  commits the grid and flag under the exclusive lock. A rejected copy falls
  back to eligible reuse or fresh work. The carried vector and stamp commit
  together.
  [Selection](../../src/rememberstack/workers/e2.py#L655-L695) ·
  [Claimify](../../src/rememberstack/workers/e2.py#L817-L860) ·
  [E1 writes](../../src/rememberstack/workers/e1.py#L515-L572) ·
  [D140 lookup and grid write](../../src/rememberstack/workers/e1.py#L167-L217) ·
  [D162 reuse gate](../../plan/designs/split_oversized_chunks_design.md#3-version-wide-reuse-gate).
- **Unarchive during later cutovers.** The reset wakes only returned rows,
  and unarchive clears the archive marker in the same transaction. D80
  serializes the whole undo with fence installation. Resets that commit
  first drain on the outgoing binding. During the fence, a retryable refusal
  preserves the archive so the undo can reset and wake eligible work after
  publication or rollback. This also covers source-driven undo.
  [Reset SQL](../../src/rememberstack/spine/lifecycle.py#L2024-L2053) ·
  [Unarchive](../../src/rememberstack/workers/reconcile.py#L1014-L1060) ·
  [D80 cutover](../../plan/designs/e1_embedding_input_policy.md#45-migration-triggers-and-vector-reuse).

## P2

- **Request limits and search readiness.** E1 batch size governs grouping.
  P1 readiness checks model, dimension and policy. D80 publishes qualified
  request-limit-only changes under the fence with current attestations and
  semantic search ready. Acceptance exercises a batch-item-limit change
  with online semantic queries and zero vector writes.
  [E1 batching](../../src/rememberstack/workers/e1.py#L388-L409) ·
  [P1 readiness](../../src/rememberstack/adapters/postgres_p1.py#L2049-L2096) ·
  [Acceptance](../../plan/designs/split_oversized_chunks_design.md#6-acceptance).
- **Replay refusal.** The outgoing ledger expects one returned row, so a
  skipped reset raises before queue announcement. The outgoing CLI
  rehearsal now expects `internal_error`, a preserved row and zero wakes.
  The incoming CLI specifies typed temporary and permanent refusals.
  [Ledger](../../src/rememberstack/spine/work_ledger.py#L1077-L1098) ·
  [Queue announcement](../../src/rememberstack/workers/operations.py#L20-L42) ·
  [Replay contract](../../plan/designs/orchestration_design.md#6-dead-letter-operations).

## Nit

- **D80 extraction reuse.** The decision interactions now require the split
  gate at commit for both versions. Extraction keys still exclude model output.
  [D80 summary](../../plan/designs/e1_embedding_input_policy.md#9-decision-interactions).

Disposable PostgreSQL 17 probes confirmed the lineage lock in both orders
and reproduced the lost reset with the outgoing SQL. A guarded retry reset
the row and woke it once. The outgoing CLI returned exit 1 and
`internal_error` for both blocks, with no state change or wake. An allowed
replay committed and woke once. Incoming integration, hosted qualification
and frozen-checkpoint equality remain release checks.
