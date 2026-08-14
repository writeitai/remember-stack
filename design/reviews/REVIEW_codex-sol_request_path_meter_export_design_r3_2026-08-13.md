# REVIEW r3 — Codex

## Verdict: REQUEST_CHANGES

R3 genuinely closes most round-2 findings. Two blockers remain: one late-commit correctness gap, and one new PostgreSQL type-resolution failure introduced by the worker-outcome fix.

## Remaining blockers

### B1 — The fixed safety lag still permits permanent cursor gaps

**Where:** [design §5.2](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:440), especially lines 448–459 and the required late-commit test at [line 744](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:744).

Changing worker timestamps from transaction-start `now()` to insert-time `clock_timestamp()` is a material improvement: the `SELECT … FOR UPDATE` wait now happens before the timestamp. It does not, however, establish a completeness boundary.

The design explicitly admits that a transaction held longer than 60 seconds can gap, but merely calling that an “operational bound” does not enforce or signal the bound.

Failure sequence:

1. Transaction A inserts receipt A with `occurred_at=t10`, then remains uncommitted for more than 60 seconds.
2. Transaction B inserts and commits receipt B with `occurred_at=t20`.
3. The exporter’s horizon reaches `t30`; its snapshot sees B but not A and advances its key beyond `t20`.
4. A commits.
5. A is permanently behind the cursor key and is never exported.

`REPEATABLE READ` only stabilizes the current request’s snapshot. It does not make replaying the same opaque cursor later stable after an older row becomes visible. The required test claiming that any short transaction committing after `horizon_at_issue` appears later is therefore stronger than the specified algorithm.

The design needs one of:

- a writer/export barrier or committed-visibility watermark;
- an enforced upper bound below `safety_lag` on insert-to-commit visibility; or
- a durable degradation signal whenever that bound is exceeded, so a skipped receipt cannot look like zero spend.

Increasing the lag without enforcing or observing it does not close the round-2 invariant.

### B2 — The r3 worker-outcome expression does not type-resolve in the union view

**Where:** surface enum at [§3.1 line 125](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:125), derived worker outcome at [§3.4 line 213](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:213).

The semantic derivation is correct. The obvious PostgreSQL projection is not executable:

- `CASE … THEN 'provider_error' ELSE 'ok' END` resolves to `text`.
- `surface_cost_ledger.outcome` is `surface_cost_outcome`.
- PostgreSQL cannot union `text` with that enum implicitly.

I verified this against PostgreSQL 16; it produces:

```text
ERROR: UNION types text and review_outcome cannot be matched
```

R2’s direct worker literal `'ok'` was an `unknown` literal and could be coerced to the enum. Wrapping the literals in the new `CASE` introduced the mismatch.

The binding view definition must choose a canonical type explicitly. Prefer casting both branches to `text` for the wire-oriented view, or cast the worker `CASE` to `surface_cost_outcome`.

## R2 closure status

| Claimed closure | R3 status |
| --- | --- |
| Worker outcome derivation | Semantically closed; executable view type remains B2 |
| Composite partitioned PK | Closed |
| pg_partman registration and premake | Closed |
| `EXPECTED_RANGE_PARENTS` / `EXPECTED_VIEWS` | Closed |
| Refresh horizon on every `next_cursor`; preserve key on empty page | Closed |
| Worker insert-time timestamp and 60-second lag | Timestamp problem closed; late visibility remains B1 |
| Counter failure fails the query | Closed in normative algorithm |
| `nominate_*` and `multi_hop_context` inventory | Closed |
| Coverage-loop multiplicity | Closed in §4.2; test typo remains |
| Wire nullability | Closed |
| Same process, daemon-thread second Uvicorn | Closed |
| `scope_missing` state and page field | Closed |

## Residual nits

1. The required scoped `answer_context` test says `N+P`, but §4.2 correctly defines testimony as `N+M` and facts as `P`. The total must be **N+M+P** at [lines 733–735](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:733).

2. Add the acceptance test for the newly binding honesty rule: force both the receipt insert and `persist_failures` upsert to fail, then assert that the user query fails. The current tests cover only a successful counter increment.

3. The “complete” amendment row for `postgres_schema_design.md` lists `surface_cost_kind` but omits the new `surface_cost_outcome` enum at [line 21](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:21).

4. Define what happens when the `scope_missing` upsert fails but the subsequent synthetic receipt insert could succeed. Otherwise request grouping can degrade without the exported counter moving.

5. The daemon-thread listener contract should say how bind/startup failure is reported. As written, the background Uvicorn thread can fail while the customer server continues normally.

6. The earlier worker-ledger limitations remain worth documenting: repeated usage-bearing failures can collide on the constant worker `provider_failure` key, and `numeric(12,6)` can still round tiny worker embeds to zero.

7. The configured drain ceiling remains 500 receipts/second. State expected production headroom or the condition under which one export worker can fall permanently behind.

## New blockers introduced by r3?

**Yes: B2.** The worker outcome derivation changes the worker branch from an enum-coercible unknown literal to a `text`-typed `CASE`, making the natural union view fail without an explicit cast.

**B1 is not new.** R3 substantially narrows the late-commit window but does not close the original completeness blocker.

No files were changed.
