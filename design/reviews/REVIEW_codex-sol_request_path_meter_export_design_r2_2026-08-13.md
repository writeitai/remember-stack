# REVIEW r2 — Codex gpt-5.6-sol xhigh

## 1. Verdict

**REQUEST_CHANGES**

The revision closes most round-1 issues, including `numeric(20,12)`, removal of surface deduplication, explicit token-host selection, CLI-only credential-file loading, async HTTP middleware, versioned immutable paths, the exact `grant_type`, separate export authentication, and the D74 exemption.

Four blockers remain.

## 2. Remaining blockers

1. **The safety horizon still permits permanent cursor gaps.**

   - **Where:** [design §5.2](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:396), [current worker transaction](/Users/jpuc/code/moje/remember-stack-m1-agent1/src/rememberstack/spine/work_ledger.py:782), [worker `occurred_at`](/Users/jpuc/code/moje/remember-stack-m1-agent1/src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py:156)
   - **Invariant:** export must be gap-free and replay-stable despite concurrent and late commits.
   - **Finding:** five seconds is a measured timing assumption, not a completeness boundary. More importantly, only surface receipts use the proposed short transaction. Worker receipts still use `DEFAULT now()`, which means transaction-start time, inside a transaction that can wait on `SELECT … FOR UPDATE`. A worker transaction can therefore commit after the horizon with an `occurred_at` already behind an advanced cursor.
   - `horizon_at_issue` freezes a timestamp predicate, not database visibility. A row committed later with `occurred_at <= horizon_at_issue` changes replay results and can be permanently skipped.
   - The protocol also does not define how an exhausted cursor obtains a later horizon: retaining the old horizon stalls forever; replacing it needs an explicit deterministic `next_cursor` rule.

2. **`persist_failures` cannot reliably report failure of its own durability domain.**

   - **Where:** [design §3.2 and §4.1](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:162)
   - **Invariant:** a successful query must leave either a durable receipt or a durable, exported loss signal.
   - **Finding:** after a receipt write fails, the counter is incremented through another transaction against the same database/pool. Pool exhaustion, database unavailability, permission/schema failure, or ambiguous commit can defeat both transactions. The design then either propagates the counter failure—contradicting fail-open query availability—or suppresses it and recreates an invisible loss.
   - A separate transaction is not a separate durability channel. The contract needs an independent durable buffer/signal or an explicit rule that the query fails when neither receipt nor loss marker can be made durable.

3. **The partitioning fix is not executable PostgreSQL schema and is catalog-incomplete.**

   - **Where:** [design §3.1 and §3.5](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:127), [catalog range parents](/Users/jpuc/code/moje/remember-stack-m1-agent1/src/rememberstack/spine/catalog_contract.py:266), [catalog views](/Users/jpuc/code/moje/remember-stack-m1-agent1/src/rememberstack/spine/catalog_contract.py:305)
   - **Invariant:** Rule 2 full-scale schema must be implementable and satisfy the executable catalog contract.
   - **Finding:** the DDL creates an ordinary table, while §3.5 says it is range-partitioned. Adding `PARTITION BY RANGE (occurred_at)` makes `PRIMARY KEY (cost_id)` invalid: a PostgreSQL partitioned-table primary key must include `occurred_at`.
   - No monthly child/pg_partman registration or partition-maintenance contract is specified.
   - The allegedly complete amendment list omits `EXPECTED_RANGE_PARENTS` for the new partitioned parent and `EXPECTED_VIEWS` for `v_cost_receipts`.

4. **The “complete current call graph” remains incomplete.**

   - **Where:** [design §4.2–§4.3](/Users/jpuc/code/moje/remember-stack-m1-agent1/plan/designs/request_path_metering_and_cost_export_design.md:248), [current `multi_hop_context`](/Users/jpuc/code/moje/remember-stack-m1-agent1/src/rememberstack/surfaces/query_engine.py:763), [its testimony retrieval](/Users/jpuc/code/moje/remember-stack-m1-agent1/src/rememberstack/surfaces/query_engine.py:934)
   - **Invariant:** one scope/request ID per public in-process operation, with exhaustive surface and call-site attribution.
   - **Finding:** the “complete” table omits public `nominate_claims`, `nominate_chunks`, and `multi_hop_context`. The latter performs the two testimony embeds. Without an explicit public-method scope mapping, its two calls can fall through to separate synthetic `library` request IDs, defeating request grouping.

## 3. Residual nits

- The ordinal is defined in the schema as per `(request_id, call_site)` but implemented conceptually as one integer per entire scope. A single integer also has no `ContextVar` “compare-and-set” operation; specify ordinary sequential `get`/`set`, initialization, and reset behavior.
- `surface_cost_scope_missing` is left as “same state table or a sibling column,” despite §4.2 calling itself an exact interface.
- `v_cost_receipts` maps every worker receipt to `outcome='ok'`, although current workers record usage-bearing provider failures. Spend remains present, but the exported outcome is misleading.
- The updated analysis is still stale in §6: it retains the original timestamp cursor and claims append-only storage alone makes replay stable.
- “Separate process listener” versus “Profile starts a second ASGI app” should use one precise process model.

The following adversarial checks are otherwise closed:

- Separate listener is compatible with Rule 3 because it ships inside the engine, is absent from the customer listener, and has a local CLI equivalent.
- D74 exemption is explicit and justified by the content-free read model.
- `numeric(20,12)` preserves tiny embed costs.
- There is no surface logical `UNIQUE` that can swallow billed calls.
- Token host is explicit, credential-file resolution is CLI/MCP-entrypoint only, and `ClientSettings` remains environment/constructor-only.
- `/ops/cost-export/v1`, frozen field assertions, and a new path for v2 close path immutability.
- `urn:ietf:params:oauth:grant-type:device_code` is stated consistently.

## 4. Did a fix introduce a new blocker?

**Yes.** The monthly-partitioning response to round-1 retention concerns introduced blocker 3: it conflicts with the declared primary key and omits the required partition/catalog machinery.
