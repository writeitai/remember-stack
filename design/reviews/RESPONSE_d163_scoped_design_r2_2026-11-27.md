# Response to the scoped D163 design review, round 2

Review of PR #682 at `04215adc5`. I accepted both P1 findings and the nit.

## P1

- **Shipped migration lock wait.** The v0.22.4 image ships the 60-second
  override, and the runner's session timeout applies to DDL as well as the
  advisory lock. The design requires implementation to remove the override
  and qualify the shipped Alembic command. Start a second previous-release
  saved query during retry backoff after `55P03` and require it to finish
  while the original reader still holds its lock.
  [Released config](https://github.com/writeitai/remember-stack/blob/v0.22.4/alembic.ini#L5-L6) ·
  [Runner](../../src/rememberstack/spine/migrations/env.py#L57-L81) ·
  [Session timeout](../../src/rememberstack/spine/migrations/env.py#L98-L114) ·
  [Migration contract](../../plan/designs/shared_retrieval_eligibility_design.md#61-d163-migration)
- **Rank and complete confirmation rows.** Scoped nomination orders by
  distinct anchor coverage and vector distance, and final confirmation
  sorts by coverage before the result cap. The oracle compares those
  nomination values and every final confirmation column. The cutoff case
  requires coverage of one when both stored endpoints resolve to the same
  requested anchor.
  [Scoped nomination](../../src/rememberstack/adapters/postgres_p1.py#L1450-L1465) ·
  [Final confirmation](../../src/rememberstack/surfaces/query_engine.py#L6208-L6249) ·
  [Qualification](../../plan/designs/shared_retrieval_eligibility_design.md#91-d163-fact-path-qualification)

## Nit

- The consumer table names the deployed `fact_eligibility_ctes` builder and
  `_fact_indexed_relation` lookup.
  [Builder](../../src/rememberstack/spine/retrieval_eligibility.py#L172) ·
  [Lookup](../../src/rememberstack/adapters/postgres_p1.py#L2609)

Full-schema equivalence, migration and latency remain implementation gates.
