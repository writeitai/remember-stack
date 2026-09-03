# Request-path metering, content-free cost export, and device-grant login

> **Binding D98 amendment (2026-08-27).** Public Cypher and Cypher explain are
> deleted surfaces. They therefore produce neither request paths nor permanent
> zero-cost rows. Plain SQL explain remains zero-provider-call; typed live-graph
> requests use the normal surface-metering outcome contract but make zero model
> calls. Pre-D98 Cypher rows in matrices below are superseded.

**Status:** accepted for implementation after dual review (Claude
APPROVE_WITH_NITS r5–r6; Codex r6 REQUEST_CHANGES was the off-by-one
forward-poll test, closed here).  
**Date:** 2026-08-13  
**Decision log:** **D91** (metering + export); **D92** (device-grant CLI).
Both are appended when this design is accepted.  
**Analysis:**
[request_path_metering_and_cost_export_analysis.md](../analysis/request_path_metering_and_cost_export_analysis.md)  
**Issue:** [#258](https://github.com/writeitai/remember-stack/issues/258)
(meter + export); [#268](https://github.com/writeitai/remember-stack/issues/268)
(login)  
**Reviews (round 1, REQUEST_CHANGES; this text is the fix):**
[REVIEW_claude-opus_request_path_meter_export_design_2026-08-13.md](../../design/reviews/REVIEW_claude-opus_request_path_meter_export_design_2026-08-13.md),
[REVIEW_codex-sol_request_path_meter_export_design_2026-08-13.md](../../design/reviews/REVIEW_codex-sol_request_path_meter_export_design_2026-08-13.md)

**Amends (complete list):**

| Document | Change |
| --- | --- |
| [postgres_schema_design.md](postgres_schema_design.md) | Add `surface_cost_kind`, `surface_cost_outcome`, `surface_cost_ledger`, `surface_cost_meter_state`, `v_cost_receipts`; add `ix_cost_export` and `outcome surface_cost_outcome NOT NULL DEFAULT 'ok'` on **existing** `cost_ledger` (existing rows backfill to `ok`, including historical billed-then-failed tiers — amounts stay; classification of pre-D91 rows is `ok`) (`processing_id` identity unchanged); worker `occurred_at` stamp is `clock_timestamp()` at INSERT (not `DEFAULT now()`); §16 decision map D91; D67 row worker-only; partition-estate table: eighth monthly RANGE family |
| [orchestration_design.md](orchestration_design.md) | Cold-reader intro and §§4/7: worker calls → `cost_ledger`; request-path calls → `surface_cost_ledger`; operator spend read model is `v_cost_receipts` |
| `decisions.md` D67 | Worker-only spend; `occurred_at` is insert-time `clock_timestamp()`; `outcome` recorded at the write site. Request-path spend is D91. |
| `spine/catalog_contract.py` | `EXPECTED_ENUMS`, `EXPECTED_TABLES`, `EXPECTED_INDEXES`, `EXPECTED_CONSTRAINT_COUNTS`, `EXPECTED_RANGE_PARENTS`, `EXPECTED_VIEWS`, per-table PK, `COMMENT ON TABLE` count, `DECISION_OBJECTS["D91"]` |
| Website (same PR as the shipping code) | `reference/api`, `reference/cli`, `configuration`, `deployment`, `project-status` |
| [open_query_space_design.md](open_query_space_design.md) | Public error-code set gains `surface_cost_unrecorded` (HTTP 503) because metering uses the spine engine |

This document is the engine contract. It does not describe a cloud control
plane. A supervisor may consume the export; correctness cannot depend on one
existing.

---

## 1. Decision

1. **Worker spend stays on `cost_ledger`.** D67 is unchanged in identity:
   `processing_id` remains `NOT NULL`, attribution is copied from a locked
   running `processing_state` row, uniqueness is
   `(deployment_id, processing_id, attempt, call_key)`. D91 **adds** an
   export index on that table. It does not null `processing_id` or change
   the money scale of worker rows.
2. **Request-path spend lives on `surface_cost_ledger`.** Interactive
   search, assured operations, lookup embeds, in-process QueryEngine
   embeds, and SQL open-query nomination embeds are not units of work and
   must not be enqueued as `processing_state`.
3. **The operator read model is one SQL view, `v_cost_receipts`.** HTTP
   export and `remember ops cost-export` are thin readers of that view.
   They cannot independently omit a ledger.
4. **Honesty guarantee (chosen):** if the receipt insert fails, increment
   `persist_failures` in a second short transaction. If **that** increment
   also fails, **fail the user-visible query**. Fail-open is allowed only
   when the loss signal is durable. Same-database total outage takes down
   export polls too (consumers see failed polls, not silent zero).
   Billed-but-invalid provider responses (`ProviderCallError.usage`)
   **are** recorded. Successful embeds always have `ProviderCallUsage`.
5. **Export HTTP is a separate listener.** It is never a route on the
   customer FastAPI app (that app’s `dependencies=` list is perimeter +
   D74 admission and applies to every route). If
   `REMEMBERSTACK_COST_EXPORT_BIND` is unset, the listener is not
   started. The customer port physically cannot serve export.
6. **`rememberstack.cost_export.v1` is an immutable path**
   `GET /ops/cost-export/v1`. A later contract is a new path. v1 never
   grows fields. Extra keys are forbidden on the page and on each receipt.
7. **`remember login` / `logout`** (D92) are CLI-only. They require an
   explicit token host. They do not change `MemoryClient` /
   `ClientSettings` defaults. The engine does not grow a second
   credential store.

### 1.1 Documented non-goals (not deferrals)

| Non-goal | Why it is out of this system |
| --- | --- |
| Interactive spend ceiling that refuses or parks a search | D67 parks **work**. Refusing a live query is a retrieval-contract change (error envelope, client retry, timeout). Operator/provider key limits remain the backstop. |
| Redacting pre-existing Uvicorn access logs of `GET /search?query=` | Those logs already contain query text today. This design makes **new** rows, export payloads, and export-specific logs content-free. Access-log redaction is a separate ops concern. |
| Cypher or SQL EXPLAIN metering | Those paths do not call the provider today. |
| `resolve` surface kind | `GET /resolve` / `QueryEngine.resolve` makes no provider call. Adding a reserved enum value “for a future embed” is undefined machinery. When resolve grows an embed, the enum and call-site vocabulary are amended together. |
| Engine-native second credential for humans | The token host already mints audited deployment tokens. |
| Push from the engine to a supervisor URL | Would make the library a client of a commercial control plane (D60/D61). |
| Export behind the customer perimeter token | Blast radius; the customer app’s dependency list cannot exempt a sibling route anyway. |

---

## 2. Why two physical ledgers

`cost_ledger` answers: *which pipeline attempt spent this money?* That
answer is a foreign key to a row workers claim, lock, retry, and
dead-letter. A search is not such a row.

`surface_cost_ledger` answers: *which inbound request spent this money?*
That answer is a request id plus a surface name.

A **unified** table with an attribution discriminator and an exactly-one-of
CHECK could also be honest (it would amend D67’s “every cost_ledger row
owns a processing attempt”). Two tables keep every existing worker writer
and budget query literally true. The view is what operators query.

`CostMeterPort` is already a **bound sink** (`call_key`, `tier`, `usage`)
and does not mention `processing_id`. A request-bound implementation of
that same protocol *could* write the surface ledger without creating fake
work. This design still uses a distinct `SurfaceCostRecorder` type
because the **bound identity** is different (request vs processing
attempt). That is a constructor-clarity choice, not a claim that reusing
the protocol recreates synthetic `processing_state`.

---

## 3. Schema

### 3.1 `surface_cost_ledger`

Worker LLM calls cost cents; a query embed can cost ~`$2e-7`. Copying
`numeric(12,6)` would round typical embeds to `$0.000000` and recreate
the under-report this design exists to stop. Surface amounts use
`numeric(20,12)`. Export serializes each ledger at its native scale as a
decimal **string** (no float).

```sql
CREATE TYPE surface_cost_kind AS ENUM (
  'search',
  'operation',
  'lookup',
  'open_query',
  'library'
);

CREATE TYPE surface_cost_outcome AS ENUM ('ok', 'provider_error');

CREATE TABLE surface_cost_ledger (
  cost_id         uuid NOT NULL,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  request_id      uuid NOT NULL,
  surface         surface_cost_kind NOT NULL,
  call_site       text NOT NULL,          -- closed vocabulary, see §4.3
  ordinal         integer NOT NULL,       -- 1-based within the request scope
  outcome         surface_cost_outcome NOT NULL,
  model_name      text NOT NULL,
  tokens_in       bigint NOT NULL,
  tokens_out      bigint NOT NULL,
  cost_usd        numeric(20,12) NOT NULL,
  latency_ms      integer NOT NULL,
  occurred_at     timestamptz NOT NULL,   -- clock_timestamp() in the recorder's own short TX
  PRIMARY KEY (cost_id, occurred_at),
  CHECK (ordinal >= 1),
  CHECK (call_site ~ '^[a-z][a-z0-9_]*$')
) PARTITION BY RANGE (occurred_at);

COMMENT ON TABLE surface_cost_ledger IS
  'Append-only provider-call attribution for request-path spend. No query text, '
  'vectors, or memory content. Distinct from cost_ledger (D67 worker attempts).';

CREATE INDEX ix_surface_cost_export
  ON surface_cost_ledger (deployment_id, occurred_at, cost_id);
```

Register the parent with **pg_partman** exactly as other monthly ledgers
(`EXPECTED_RANGE_PARENTS`, `part_config` interval `'1 mon'`). The
migration creates the parent, the partman config row, and the first
premake children. An unregistered parent would stop accepting inserts at
the first month boundary; that is forbidden.

There is **no** UNIQUE on `(request_id, call_site, ordinal)`. The request
path has no worker-style redelivery of the same attempt. A uniqueness
constraint here can only swallow a second billed call. Dedup for
consumers is `(deployment_id, source, cost_id)`.

`cost_id` remains the consumer identity (UUID). The composite PK exists
only because PostgreSQL requires the partition key in the primary key.

`call_site` is a closed token (`search_claims`, `fact_context`, …). It is
never interpolated from query text. The CHECK is a defense; the writer
uses an enum.

### 3.2 Meter-degradation state

```sql
CREATE TABLE surface_cost_meter_state (
  deployment_id        uuid PRIMARY KEY REFERENCES deployments,
  persist_failures     bigint NOT NULL DEFAULT 0 CHECK (persist_failures >= 0),
  scope_missing        bigint NOT NULL DEFAULT 0 CHECK (scope_missing >= 0),
  last_failure_at      timestamptz
);

COMMENT ON TABLE surface_cost_meter_state IS
  'Monotonic count of surface-meter persist failures. Exported so a supervisor '
  'can tell lost receipts from genuine zero spend.';
```

### 3.3 Worker export index (identity unchanged)

```sql
CREATE INDEX ix_cost_export
  ON cost_ledger (deployment_id, occurred_at, cost_id);
```

`ix_cost_budget_window (deployment_id, stage, lane, occurred_at)` cannot
serve a cross-stage union ordered by `(occurred_at, cost_id)`.

### 3.4 `v_cost_receipts` (the single read model)

Allowlist columns only — the same fields as the v1 receipt. Implementation
projects:

| View column | Worker | Surface |
| --- | --- | --- |
| `source` | `'worker'` | `'surface'` |
| `cost_id` | `cost_ledger.cost_id` | `surface_cost_ledger.cost_id` |
| `deployment_id` | both | both |
| `work_id` | `processing_id` | `request_id` |
| `stage` | `stage` | NULL |
| `lane` | `lane` | NULL |
| `attempt` | `attempt` | NULL |
| `surface` | NULL | `surface` |
| `call_key` | `call_key` | `call_site \|\| ':' \|\| ordinal` |
| `outcome` | `cost_ledger.outcome::text` (set at write site) | `outcome::text` |
| `model_name`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `occurred_at` | native | native |

No query text. No `memory_v1` publication of this view. The sandbox
query role continues to receive `SELECT` only on `memory_v1` objects.
Publishing spend through open query would put the export behind the
customer perimeter; that is rejected.

### 3.5 Retention and scale

`surface_cost_ledger` is **range-partitioned by month** on `occurred_at`
via pg_partman (`'1 mon'`), matching `testimony_currency_events` /
`mentions`. The default deployment **keeps every partition**. An
operator who `DROP`s an old partition has chosen to forget those
receipts; a cursor into a dropped range returns no rows. There is no
silent GC.

`cost_ledger` retention is unchanged (existing worker policy).

Interactive **ceilings** are a non-goal (§1.1). Provider-key limits remain.

---

## 4. Recording request-path calls

### 4.1 Recorder

```text
record(*, usage: ProviderCallUsage, outcome: SurfaceCostOutcome) -> None
```

The recorder is bound at construction to `deployment_id` plus the current
request scope (below). It does not accept query text. It uses
`usage.latency_ms` (one authority; `ProviderCallUsage` already has it).

**Worker `outcome` is written, not inferred.** Add
`cost_ledger.outcome surface_cost_outcome NOT NULL DEFAULT 'ok'`.
`_LedgerCostMeter.record` writes `ok`. `_record_failed_provider_usage`
and every other `ProviderCallError.usage` writer writes
`provider_error`. New failure sites cannot forget: the method that
accepts usage-from-error **requires** `outcome=provider_error`. The
view projects `outcome::text` on both branches (enum UNION text is
illegal). Do not reconstruct outcome from `tier` string matching.

Persist algorithm:

1. Open a **dedicated short transaction** on the **same spine engine**
   the export listener reads. Do **not** piggy-back the query’s
   sandbox/query-role connection or a long-held interactive transaction.
2. `INSERT` the row with `occurred_at = clock_timestamp()` (statement
   time of this short TX, which commits immediately after).
3. Commit. Target duration: milliseconds.
4. On insert failure: in a **second** short TX,
   `INSERT INTO surface_cost_meter_state (deployment_id, persist_failures, last_failure_at)
    VALUES (:id, 1, clock_timestamp())
    ON CONFLICT (deployment_id) DO UPDATE
    SET persist_failures = surface_cost_meter_state.persist_failures + 1,
        last_failure_at = EXCLUDED.last_failure_at`;
   log the literal `surface_cost_record_failed`; return without raising.
5. If that increment TX also fails: **raise**
   `SurfaceCostUnrecordedError` to the query. HTTP maps it to **503**
   with a stable public detail `surface_cost_unrecorded` (retryable;
   no query text). `POST /query/sql` can fail even when the sandbox
   pool is healthy, because metering uses the spine engine — that is
   intended. Fail-open is permitted only after a durable loss signal.
   A page with no meter-state row reports `persist_failures: 0` and
   `scope_missing: 0`.

   `scope_missing` increment uses the same upsert. If it fails and the
   subsequent receipt insert could still succeed: **skip the insert and
   raise** `SurfaceCostUnrecordedError` (do not record under a
   synthetic scope without a durable `scope_missing` bump).

### 4.2 Request scope (exact interface)

**Immutable context only.** A `ContextVar` holds a frozen

```text
SurfaceRequestScope(request_id: UUID, surface: SurfaceCostKind)
```

or `None`. No counters in the context object.

**HTTP (customer app):** an **async** middleware (not a sync `Depends`)
opens the scope before the sync endpoint runs in the threadpool, and
resets the token in `finally`. FastAPI copies context into worker
threads from the async task; a scope set in a sync dependency is
invisible to the endpoint. A test must prove two embeds in one
`answer_context` HTTP call share `request_id`.

**Assured operations / QueryEngine public methods:** if a scope is
already set, reuse it (nested). If not, the public method opens
`(uuid4(), surface_for_that_method)` and resets in `finally`.

**SQL sandbox:** `QuerySandboxExecutor._run` already mints `request_id`
(`executor.py`). **That method** sets the ContextVar to
`(that request_id, open_query)` before calling `embed=`, and resets in
`finally`. Cypher and SQL EXPLAIN do not embed and do not open a cost
scope.

**Missing scope at the recorder:** mint a synthetic
`(uuid4(), library)`, increment `surface_cost_meter_state.scope_missing`
(same upsert shape as persist_failures), log the literal
`surface_cost_scope_missing`, still record. Export pages include
`scope_missing`. Production HTTP tests fail if this counter moves.

**Surface mapping (complete, current call graph):**

| Entry | Surface | Embeds |
| --- | --- | --- |
| `GET /search/claims`, `GET /search/chunks` (semantic) | `search` | 1 |
| same, `channel=bm25` | `search` | 0 |
| `POST /operations/{name}` `testimony_context` | `operation` | 2 on the unscoped path; **N+M** when entity-scoped coverage loops re-embed the same query per tier (`_coverage_ordered_nominations`). Ordinal absorbs N. |
| `POST /operations/{name}` `fact_context` | `operation` | 1 unscoped; **P** with eligibility coverage loop |
| `POST /operations/{name}` `answer_context` | `operation` | testimony + fact (not a fixed 3 when scoped) |
| `GET /lookup/observations?property_query=` | `lookup` | 1 |
| `POST /query/sql` (execute) | `open_query` | 0..n |
| `POST /query/sql/explain`, Cypher, Cypher explain | — | 0 |
| `GET /resolve` | — | 0 |
| In-process `claims_about` / `claims_as_of` | `library` | 1 when ranking embeds |
| In-process `nominate_claims` / `nominate_chunks` | `library` | 1 semantic |
| `examples.multi_hop_context` through `POST /query/sql` | `open_query` | 0; graph and text retrieval remain provider-free SQL |

### 4.3 Closed `call_site` vocabulary

Writers pass a `SurfaceCallSite` enum. `_embed` takes
`call_site: SurfaceCallSite` explicitly. The ordinal is a second
ContextVar holding an `int`, initialized to `0` when the scope opens,
reset in the same `finally`. Sequential embeds in one sync endpoint
share one context copy: `ordinal = get() + 1; set(ordinal)`. There is
no ContextVar compare-and-set. Concurrent embeds inside one request
are not in the current call graph; do not add them without replacing
this with a lock.

Repeated embeds of the **same query string** (coverage-tier loops) are
sequential and **are** in the current call graph. They share
`call_site` and increment `ordinal`. That is why ordinal exists.

Initial enum members:

`search_claims`, `search_chunks`, `lookup_observations`,
`testimony_claims`, `testimony_chunks`, `fact_context`,
`claims_about`, `claims_as_of`, `nominate_claims`, `nominate_chunks`,
`open_query_sql`.

Export `call_key` is `f"{call_site.value}:{ordinal}"`. Adding a member
is a catalog/code change, not a stringly new site.

Unscoped `testimony_context` calls the public `nominate_claims` /
`nominate_chunks` helpers and therefore records those `call_site`
values. The `testimony_*` members are the **coverage-loop** sites
only. `_nominate_claim_ids`, `_nominate_chunk_ids`, and
`_rank_bounded_claims` are plumbing: they take `call_site` from the
public caller and must not hardcode one.

Worker residual (named, not fixed here): `_record_failed_provider_usage`
uses a constant `call_key="provider_failure"` under D67’s UNIQUE, so a
second billed failure in one attempt is `ON CONFLICT DO NOTHING`.
Tiny worker embeds still round at `numeric(12,6)`. Both remain D67
worker-ledger facts; the surface ledger does not inherit them.

### 4.4 Provider outcomes

```text
try:
    response = provider.embed(...)
    recorder.record(usage=response.usage, outcome=ok)
    return response.vectors[0]
except ProviderCallError as error:
    if error.usage is not None:
        recorder.record(usage=error.usage, outcome=provider_error)
    raise
```

`ProviderAccountingError` on a path that would otherwise succeed remains
a hard failure (the model contract: a paid response without accounting
must not be treated as success). It is not converted into a NULL-usage
row.

### 4.5 Production wiring

`SelfHostProfile` constructs the SQL recorder, passes it to
`QueryEngine`, wraps `selfhost_embed_query` for SQL execute, and
installs the async scope middleware. Startup asserts the recorder is
durable (not a no-op). Tests inject a recording fake.

`QueryEngine` is **not** given a second `deployment_id` on the
constructor (public methods already take it). The recorder is bound to
the profile’s deployment; a method call with a different
`deployment_id` logs `surface_cost_deployment_mismatch` and records
under the method’s `deployment_id` only if it matches the recorder;
otherwise it increments `persist_failures` and skips the insert
(single-deployment process; mismatch is wiring corruption).

---

## 5. Export contract `rememberstack.cost_export.v1`

### 5.1 Path and versioning

```text
GET /ops/cost-export/v1?cursor=<optional>&limit=<1..500, default 100>
```

- v1 lives at this path forever with a frozen field set.
- A later contract is `GET /ops/cost-export/v2`. v1 is not mutated.
- Unknown version path → 404.
- Checked-in golden page JSON + a test that the Pydantic model’s field
  set equals the frozen list (not merely `extra="forbid"`, which cannot
  catch a *declared* new field).

Page (`extra` forbid):

```text
{
  "contract": "rememberstack.cost_export.v1",
  "deployment_id": "<uuid>",
  "server_time": "<rfc3339 UTC>",
  "horizon": "<rfc3339 UTC>",
  "cursor": "<opaque>",
  "next_cursor": "<opaque>",
  "persist_failures": <int>,
  "scope_missing": <int>,
  "receipts": [ <receipt>, ... ]
}
```

Receipt (`extra` forbid) — exact field set:

`cost_id`, `deployment_id`, `source`, `work_id`, `stage`, `lane`,
`attempt`, `surface`, `call_key`, `outcome`, `model_name`, `tokens_in`,
`tokens_out`, `cost_usd`, `latency_ms`, `occurred_at`.

`source` is `worker` | `surface`. A supervisor that uses UMC names maps
`worker` → `engine_cost_ledger` and `surface` → `engine_surface_meter`.
That mapping is normative for UMC; the engine wire uses the short names
so the library does not encode a commercial product’s enum.

Wire types (frozen; nullability is part of the contract):

| Field | Type | Null |
| --- | --- | --- |
| `cost_id`, `deployment_id`, `work_id` | UUID string | no |
| `source` | `worker` \| `surface` | no |
| `call_key`, `outcome` | string | no |
| `stage`, `lane`, `attempt` | string / int | **yes** (null on surface rows) |
| `surface` | string | **yes** (null on worker rows) |
| `model_name` | string | **yes** on worker rows (`cost_ledger.model_name` is nullable); no on surface rows |
| `tokens_in`, `tokens_out`, `latency_ms` | int | **yes** on worker rows; no on surface rows |
| `cost_usd` | decimal string | **yes** on worker rows (shipped column is nullable); no on surface rows |
| `occurred_at` | RFC 3339 UTC | no |

A NULL worker `cost_usd` serializes as JSON `null`, not as `"0"`. The
page must not 500 on a nullable worker field. Parse `cost_usd` as
decimal, never compare strings (`numeric(12,6)` vs `numeric(20,12)`).

Idempotent consumer key: `(deployment_id, source, cost_id)`.

### 5.2 Cursor and late-commit safety

**Stamp time is insert time on both ledgers.** Surface rows use
`clock_timestamp()` in their own short TX. Worker `record_call` runs in **its own** `engine.begin()` (already
true in `work_ledger.py`) and **sets `occurred_at = clock_timestamp()`
on the INSERT** (overrides `DEFAULT now()`). **Writer barrier (enforced):** after the cost
`INSERT`, the next statement is `COMMIT`. No other SQL is permitted
between them. Both writers run, **before the INSERT**:

```text
SET LOCAL statement_timeout = '15s';
SET LOCAL idle_in_transaction_session_timeout = '15s';
```

(`statement_timeout` bounds each command; `idle_in_transaction_session_timeout`
bounds the INSERT→COMMIT idle gap — the repo already uses this GUC on
the query role. Both must stay **below** `safety_lag`. Sync-replication
commit wait is not aborted by either GUC; operators who enable
synchronous_commit=on remotely must keep replica lag under
`safety_lag` or accept a named residual.)

A TX that cannot finish in that window aborts: no visible row.
Required test: idle after INSERT longer than the idle timeout →
transaction aborted, no exported row. Surface then follows
§4.1 (increment `persist_failures` or fail the query). Worker
`record_call` raises; the handler fails as today.

This is the completeness bound: a committed row cannot have sat
idle-in-transaction longer than `idle_in_transaction_session_timeout`,
and no single statement can exceed `statement_timeout`. The
insert-then-hold-then-commit sequence aborts.

Export uses a **REPEATABLE READ** snapshot. `SELECT clock_timestamp()`
is the **first** statement of the export transaction (it establishes
the snapshot). `server_time` and `request_horizon` come from that
value, not the exporter process clock.

```text
request_horizon = server_time - safety_lag
```

`safety_lag` starting point to measure: **60 seconds**. Candidate rows:
`occurred_at <= upper_bound`.

**Upper bound for this request:**

- no cursor → `request_horizon`
- cursor present → `min(cursor.horizon_at_issue, request_horizon)`
  (replay-stable, and a corrupt/future cursor cannot skip rows)

Cursor payload:

```text
(last_occurred_at, last_source, last_cost_id, horizon_at_issue)
```

**Every response** (including empty):

```text
next_cursor = (
  last returned key OR incoming key OR zero key,
  THIS request's request_horizon   -- always a fresh horizon
)
```

So:

- **Replay** (same cursor string twice) → same receipts (frozen bound).
- **Forward** (use the `next_cursor` from the last response) → new
  bound, so rows that have since aged past `safety_lag` appear.

Required tests:

1. Same cursor twice, insert between them → **same** receipt ids
   (replay).
2. Empty page at `t0` yields `NC1` with frozen horizon `H1`. Insert a
   row, wait `> safety_lag`, poll **`NC1` once** → still empty (bound
   is still `H1`); that response issues `NC2` with a fresh horizon.
   Poll **`NC2`** → the row appears. Steady-state export latency is
   `poll_interval + safety_lag`, not `safety_lag` alone. A cursor
   issued *before* the wait cannot incorporate a future horizon
   without breaking replay.

- Empty `receipts` is **healthy** when the listener is up.
- **Transport liveness** = successful pages (`server_time` moves).
- **Spend progress** = new receipts. The engine does not classify an
  empty page as a stall.

Consumer obligations (not engine code):

- Apply idempotently on `(deployment_id, source, cost_id)`.
- Do not invent `cost_usd` when polls fail.
- Treat **rising `persist_failures`** as lost receipts, not as zero
  spend.
- Treat **failed polls** (network, 5xx) as exporter-down, not as zero
  spend.

### 5.3 HTTP producer topology (binding)

**Same OS process, second bind.** `remember-selfhost api` stays one
process. When `REMEMBERSTACK_COST_EXPORT_BIND` is set, the profile
starts a **second uvicorn server in a daemon thread** serving a
separate ASGI app that mounts **only** `/ops/cost-export/v1`. It does
not import `build_api`. D74 admission does not apply. Token rotation
restarts the whole API process (there is no independent export
process). Examples: `127.0.0.1:8001`, `[::1]:8001`,
`unix:/run/rememberstack/cost-export.sock`.

If the bind is unset, there is no HTTP export. Local operators use the
CLI. This is the mechanical Rule 3 boundary: the customer listener
cannot grow the route, and a proxy in front of the customer port cannot
forward a path that does not exist there.

The export thread starts from the customer app’s **lifespan/startup**
hook (not from merely constructing `create_api()`). If the export
server fails to bind, **the whole API process refuses to start**
(fail closed when the operator asked for HTTP export). Drain ceiling:
1 req/s × 500 = 500 receipts/s; one worker is enough unless a
deployment exceeds that sustained rate (measure).

**Auth:** `Authorization: Bearer <token>` compared in constant time to
`REMEMBERSTACK_COST_EXPORT_TOKEN`. Wrong or missing → 401, no echoed
secret. These two settings live on a dedicated settings model with env
prefix `REMEMBERSTACK_` (not `REMEMBERSTACK_SELFHOST_`), field names
`cost_export_token` and `cost_export_bind`.

If the bind is set and the token is unset or shorter than 32 bytes, the
profile **refuses to start** (fail closed). A listening unauthenticated
export is worse than no export.

**Rate limit:** one in-process token bucket, starting point **1 req/s**.
Operators run **one** export worker (documented). Multi-worker would
multiply the bucket; that is not the composed default. Rotation: change
the token and restart the export listener; in-flight polls 401 until
updated.

Do not log Authorization headers or tokens.

### 5.4 Local producer

`remember ops cost-export --deployment <uuid> [--cursor …] [--limit …]`
requires the server extra, reads `v_cost_receipts` + meter state, prints
**one** v1 page as JSON on **stdout**. Logs go to stderr. Exit 0 on a
well-formed page (including empty receipts). `--deployment` is required
to match existing `remember ops` grammar even though the spine is one
deployment (D50); mismatch → exit 2.

---

## 6. Human login and logout (D92)

This is a **CLI** feature. `MemoryClient` and `ClientSettings` continue
to resolve **only** from constructor arguments and `REMEMBERSTACK_API_*`
environment variables. They **do not** read the credential file. Ambient
file pickup would send an embedded library to a host the caller never
configured.

### 6.1 Commands

```text
remember login --token-host URL [--api-url URL]
remember logout [--token-host URL]
```

`--token-host` is **required** on login (or `REMEMBERSTACK_TOKEN_HOST`).
There is **no** derivation of a token host from `--api-url`. The engine
must not encode a commercial control plane’s `/dp/v1` layout.

`--api-url` on login is an explicit query-API override stored in the file. If
it is omitted, login derives `https://{data_plane_hostname}` from a live
hostname advertised by the token host. It does not fall back to
`REMEMBERSTACK_API_URL` or localhost: doing so could bind a newly minted
deployment credential to an unrelated endpoint. A self-hosted or local token
host that does not advertise a hostname therefore requires the flag. The query
API is not the device-grant host.

Without the explicit override, the hostname must be present, structurally
valid, and advertised as live. Concretely, it is one printable ASCII host or
`host:port`, with no scheme, path, query, fragment, userinfo, or whitespace; a
port is 1..65535, and the host is either an IP literal or nonempty DNS labels
of at most 63 characters and 253 characters in total. Underscore labels and
one trailing DNS root dot are accepted; an empty label, including one left by
a second trailing dot, is not. A missing hostname asks for `--api-url`; an
invalid hostname or a present hostname whose live flag is false or null exits
nonzero and prints the hostname and reason. These checks occur after the token
is minted, so every refusal withdraws the new credential (or keeps its secret
in the pending-revocation journal when withdrawal cannot be confirmed) and
does not write or replace `credentials.json`.

`logout` uses `--token-host`, else `REMEMBERSTACK_TOKEN_HOST`, else the
file’s `token_host`. It does not take `--api-url`.

### 6.2 Device-grant HTTP (token host)

JSON, `Content-Type: application/json`. This is the **token host’s**
contract (UMC device-grant v1), not RFC 8628 form-encoding. Only the
`grant_type` URN is borrowed from the RFC. A form-only host is a
different contract.

**Authorize** `POST {token_host}/v1/device/authorize`

Request body (optional): `{ "client_name": "remember-cli" }` (`client_name`
max 64 chars). Empty body is accepted.

Success **200**:

```text
{
  "device_code": "<string>",
  "user_code": "<string>",
  "verification_uri": "<url>",
  "verification_uri_complete": "<url>",
  "expires_in": <int seconds>,
  "interval": <int seconds>
}
```

Print `user_code` and both URIs. Do not print `device_code`.

**Poll** `POST {token_host}/v1/device/token`

```text
{
  "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
  "device_code": "<from authorize>"
}
```

The CLI sends this exact `grant_type` string. A token host that uses a
different URN is a different contract and needs a design amendment, not
a silent client guess.

Poll errors are HTTP **400** with body
`{ "error": "<code>", "error_description": "<string>" }` (no extra
keys). Poll every `interval` seconds (cap 30s). `slow_down` → increase
sleep by 5s, cap 30s. `authorization_pending` → continue.
`expired_token`, `access_denied`, `invalid_grant` → exit 1 with
`error_description`. `temporarily_unavailable` → back-off until
`expires_in` elapses. Respect `Retry-After` when present. **Do not
follow cross-host redirects** on authorize or token (host-preserving
same-origin redirects only). Ctrl-C / SIGINT: exit 130, write no file,
leave no device_code on disk.

Success **200**:

```text
{
  "access_token": "<secret>",
  "token_type": "Bearer",
  "token_id": "<uuid>",
  "org_id": "<uuid>",
  "deployment_id": "<uuid>",
  "label": "<string>",
  "token_prefix": "<string>",
  "data_plane_hostname": "<hostname or null>",
  "data_plane_hostname_live": "<boolean or null>"
}
```

The two data-plane fields let login bind the new credential to its deployment.
Older or self-hosted token services may omit them; that is the missing-hostname
case in §6.1. A null live flag is treated as false. The response model ignores
additional fields so the separately deployed token service can evolve without
breaking older clients.

TTL: if authorize’s `expires_in` elapses before 200, stop. Do not keep
polling a dead grant.

### 6.3 Credential file

Directory (first existing, else create the first):

1. `$REMEMBERSTACK_CONFIG_DIR`
2. `$XDG_CONFIG_HOME/rememberstack`
3. `~/.config/rememberstack`

File: `credentials.json`. Create with `0600` from the first byte
(`os.open` with `O_CREAT|O_EXCL` or write-to-temp + `os.replace` in the
same directory), `fsync`, refuse to follow a symlink at the final path.
Directory `0700`.

```text
{
  "version": 1,
  "api_url": "<query API>",
  "token_host": "<explicit token host>",
  "access_token": "<secret>",
  "token_type": "Bearer",
  "token_id": "<uuid>",
  "org_id": "<uuid>",
  "deployment_id": "<uuid>",
  "label": "<string>",
  "token_prefix": "<string>"
}
```

`extra` forbid; `version` is the discriminator. Unknown version → refuse
to read.

Never print `access_token`. `login` may print `token_prefix`,
`deployment_id`, `api_url`.

**Second login while a file exists:** attempt `logout` (revoke + unlink)
first; if revoke is already-revoked (`401`/`404`), unlink and continue;
if revoke is 5xx, abort (do not leave a second live token + a replaced
file without saying so). Then write the new file.

### 6.4 Logout

`DELETE {token_host}/v1/api-tokens/self` with the stored bearer.

| Revoke result | File | Exit |
| --- | --- | --- |
| 2xx | unlink | 0 |
| 401 / 404 (already dead) | unlink | 0 |
| 5xx / network | **keep** | 1 |
| no file | n/a | 0 |

### 6.5 CLI credential use

Only the `remember` CLI (and `remember mcp` stdio entry) loads the file,
after flags and env:

1. `--token` / `--api-url` on the subcommand, when present.
2. `REMEMBERSTACK_API_AUTHORIZATION` / `REMEMBERSTACK_API_URL`.
3. Credential file (CLI only).
4. SDK defaults.

The CLI then constructs `MemoryClient(base_url=..., authorization=...)`.
`--token` may be raw or `Bearer …`.

Filesystems without POSIX modes: write best-effort and warn; refuse to
read if the platform reports a world-readable mode.

---

## 7. Failure and recovery

| Failure | Behaviour |
| --- | --- |
| Provider embed succeeds, meter insert fails, counter succeeds | Query succeeds; `persist_failures++`; log `surface_cost_record_failed` |
| Insert **and** counter fail | `SurfaceCostUnrecordedError` → HTTP 503 `surface_cost_unrecorded` |
| Provider billed then failed validation | Row with `outcome=provider_error`; original error propagates |
| Provider success without usage | `ProviderAccountingError`; no row; query fails |
| Export bind set, token missing/short | Process refuses to start |
| Export bind unset | No HTTP export |
| Export token wrong | 401 |
| Export cursor malformed | 422, no receipts |
| Empty page inside horizon | 200 heartbeat |
| Login without `--token-host` / env | Exit 2; no derive-from-api-url |
| Login without `--api-url` or an advertised hostname | Exit 1; ask for `--api-url`; withdraw the mint; keep any existing file |
| Login with an invalid advertised hostname | Exit 1; print the hostname; ask for `--api-url`; withdraw the mint; keep any existing file |
| Login with a hostname that is not live | Exit 1; print the hostname; withdraw the mint; keep any existing file |
| Logout revoke 5xx | Keep file; exit 1 |
| Credential file world-readable | Refuse to read |

---

## 8. Tests required

- Semantic search writes one surface row; BM25 writes zero.
- HTTP unscoped `answer_context` writes three rows, **same**
  `request_id`; scoped coverage loops write **N+M+P** rows, still one
  `request_id`.
- Force insert **and** `persist_failures` upsert to fail → query
  raises `SurfaceCostUnrecordedError` / HTTP 503.
- `examples.multi_hop_context` through SQL execute writes no model-cost rows and
  carries the sandbox `request_id` through its ordinary query audit record.
- Empty page → `NC1`; insert; wait `> safety_lag`; poll `NC1` still
  empty; poll returned `NC2` → row appears.
- Same cursor twice with an insert between → identical receipt ids.
- Idle after INSERT longer than idle timeout → TX aborted, no row.
- SQL execute embed → `open_query` and the sandbox `request_id`.
- Cypher / SQL explain → zero surface rows.
- `ProviderCallError` with usage → `outcome=provider_error` row.
- Worker `record_call` writes only `cost_ledger`.
- Recorder uses its own TX: a long-held query transaction cannot hide
  the row from export after horizon.
- Late-commit: a row whose short TX commits after a page’s
  `horizon_at_issue` appears on a **later** cursor, not a skipped hole
  behind an advanced watermark.
- Same cursor replay → same `cost_id`s.
- Empty page: `next_cursor` carries horizon; `server_time` present.
- Golden v1 field set (page + receipt) matches the frozen list.
- HTTP export is **not** registered on `build_api`.
- Export listener accepts export token; rejects a customer perimeter
  token; D74 forget-in-progress on the **customer** app does not stop
  the export listener.
- `persist_failures` increments when insert is forced to fail; page
  reports the new value.
- Tiny embed `cost_usd` (`1e-9`) round-trips at `numeric(20,12)`.
- `call_site` with a non-token string is rejected by CHECK / enum.
- Login writes `0600`; does not read via `ClientSettings`.
- Logout 401 unlinks; logout 503 keeps file.
- Second login after 503 revoke aborts.

---

## 9. Documentation (same PR as the shipping code)

| Page | Content |
| --- | --- |
| `website/src/app/docs/reference/api/page.mdx` | Export lives on the ops listener, path `/ops/cost-export/v1`, not on the query API |
| `website/src/app/docs/reference/cli/page.mdx` | `login`, `logout`, `ops cost-export` |
| `website/src/app/docs/configuration/page.mdx` | `REMEMBERSTACK_COST_EXPORT_TOKEN`, `REMEMBERSTACK_COST_EXPORT_BIND`, `REMEMBERSTACK_TOKEN_HOST`, `REMEMBERSTACK_CONFIG_DIR` |
| `website/src/app/docs/deployment/page.mdx` | Separate bind; one export worker; do not put export on the public query port |
| `website/src/app/docs/project-status/page.mdx` | Truthful: request-path metering and export exist when this ships |

Docs describe what the tree runs.

---

## 10. Alternatives rejected

| Alternative | Why it lost |
| --- | --- |
| Synthetic `processing_state` for search | Work queue is not a request log |
| Nullable `cost_ledger.processing_id` | Breaks D67 lock-and-copy |
| Unified discriminator table | Honest, but forces every budget SQL and D67 sentence to grow branches; two tables + one view keep worker code literally true |
| Metrics/logs as the record | Not replay-safe |
| Push to a cloud URL | Library becomes a cloud agent |
| Export route on `build_api` | Unimplementable exemption from app-level `Depends`; also inherits D74 503 |
| Customer token for export | Blast radius |
| Derive token host from `--api-url` | Wrong-host footgun; encodes a commercial URL layout |
| Ambient credential file in `MemoryClient` | Embedded callers silently acquire a human cloud token |
| UNIQUE `(request_id, call_site, ordinal)` | No redelivery path; collisions hide spend |
| `numeric(12,6)` on surface amounts | Rounds typical embeds to zero |
| Fail the user query when meter insert fails | Availability; the exported counter is the honesty channel |
| Fail-open with only a log line | Invisible to remote consumers |
| Reserved `resolve` enum | Speculative future machinery |

---

## 11. Implementation-facing map

| Concern | Home |
| --- | --- |
| DDL | Alembic after `p9_10_0031` (or current head at implement time) |
| Recorder SQL | `spine/` sibling of `work_ledger`, not a `record_call` overload |
| QueryEngine | `_embed(..., call_site=)`; recorder injected; no extra constructor `deployment_id` |
| SQL embed wrap | `selfhost_embed_query` + sandbox `request_id` |
| Customer HTTP scopes | async middleware in `build_api` |
| Export HTTP | same process, daemon-thread uvicorn, second bind; not `build_api` |
| Worker stamp | `record_call` INSERT sets `occurred_at = clock_timestamp()` |
| `remember budget` | stays **worker-only** (D67 route ceilings). Total spend is `ops cost-export` / `v_cost_receipts`. |
| Settings | `REMEMBERSTACK_COST_EXPORT_TOKEN`, `REMEMBERSTACK_COST_EXPORT_BIND` |
| CLI export | `remember ops cost-export` |
| CLI login | `remember login` / `logout` in the base extra; file load only there |
| Catalog | §1 amend list |
| D67 prose | worker-only after D91 |

---

## 12. Dual-review disposition (round 1)

| ID | Reviewer | Disposition in this revision |
| --- | --- | --- |
| Cursor late-commit | both r1+r2 | `clock_timestamp()` on **both** ledgers; 60s horizon; **every** `next_cursor` refreshes horizon and keeps key |
| Export vs app Depends | both | Separate listener; bind unset → no HTTP |
| Fail-open invisible | both | `persist_failures` on every page; billed errors recorded |
| `numeric(12,6)` | Claude | `numeric(20,12)` on surface |
| UNIQUE swallows spend | Claude | Dropped |
| Access-log claim | both | Narrowed; redaction is a non-goal |
| Rule 2 ceiling/retention | Claude | Ceiling non-goal with reason; monthly partitions, no silent GC |
| v1 path versioning | both | `/ops/cost-export/v1`; golden field set |
| token_host derive | both | Required explicit host |
| SDK file pickup | Claude | CLI only |
| Scope / ContextVar | both | Async middleware; explicit `call_site`; immutable scope |
| Wrong embed inventory | both | Cypher/EXPLAIN 0; answer_context 3; resolve removed |
| Catalog list | both | Full amend table |
| CostMeterPort reuse | Codex | Corrected: protocol can be reused; distinct type for bound identity |
| Union view | both | `v_cost_receipts` |
| D91 vs D67 | both | D91 new; D67 amended to worker-only |
| Login as own decision | Codex | D92 |
