# Request-path metering and content-free cost export — analysis

**Status:** analysis (non-binding)  
**Date:** 2026-08-13  
**Issue:** [#258](https://github.com/writeitai/remember-stack/issues/258)  
**Intended binding successor:**
[request_path_metering_and_cost_export_design.md](../designs/request_path_metering_and_cost_export_design.md)

## 1. Problem frame

A RememberStack deployment spends provider money in two places:

1. **Workers** (ingest pipeline). Each billed OpenRouter call is written to
   `cost_ledger` through `WorkLedger.record_call`. Attribution is copied from a
   **running** `processing_state` row (D67): stage, lane, attempt. The row is
   idempotent on `(deployment_id, processing_id, attempt, call_key)`.
2. **The request path** (search, assured operations / “recipes”, open-query
   embeds). The same provider returns `usage.cost`. The API process **drops
   that number**.

The concrete drop sites on current `main` (inspected 2026-08-13):

| Call site | What it does | Usage fate |
| --- | --- | --- |
| `QueryEngine._embed` | Embeds the query string for semantic search, observation lookup, testimony/fact context | Returns `response.vectors[0]` only |
| `selfhost_embed_query` | Embeds for **SQL execute** nomination only (Cypher and SQL EXPLAIN do not embed today) | Same drop |
| Worker handlers via `_LedgerCostMeter` | Pipeline embeds and LLM calls | Persisted |

`adapters/openrouter.py` already parses `usage.cost` into `ProviderCallUsage`.
Nothing new is required from the provider. The engine computes the number and
then forgets it for every interactive embed.

That is dishonest for a self-hoster (“my OpenRouter bill moved — what did
it?”). It is also the remaining measurement hole for a supervising system that
wants work-attributed receipts for search and operations, not only ingest.

This analysis does **not** design cloud billing, margin, or multi-tenant
routing. The engine reports **provider spend only**. Receipts carry **no
memory content** (no query text, no chunks, no claims).

## 2. What “wrong” costs

| Wrong outcome | Cost |
| --- | --- |
| Keep dropping request-path usage | `cost_ledger` systematically under-reports; heavy retrieval against a small corpus can be most of the bill and still look free |
| Stuff request-path rows into `processing_state` | Pollutes the work queue; `record_call` requires `status='running'`; crash leaves stale-running rows (already a known recovery hazard); `pipeline_stage` has no search/operation value; budget windows start mixing interactive spend with worker lanes |
| Put export on the customer perimeter credential | A leaked deployment token can read every cost row; a cloud proxy that forwards the customer path would expose a management surface |
| Denylist “don’t include prompt text” | One missed field ships content. Allowlist-only is the only durable rule |
| Invent settled money on a silent exporter | Supervisors treat silence as zero spend |

## 3. Constraints that are real

1. **D67 is not optional.** `cost_ledger.processing_id` is `NOT NULL` and
   foreign-keys `(deployment_id, processing_id)` → `processing_state`.
   `WorkLedger.record_call` locks that row and refuses anything not
   `running`. Attribution fields are copied from the row; callers cannot
   supply them.
2. **`pipeline_stage` is a worker vocabulary.** Current enum values are
   ingest/convert/structure/chunk/embed/extract/normalize/…/hard_forget.
   There is no `search` or `operation` stage, and adding one would invite
   workers to claim interactive rows.
3. **Request-path calls have no processing target.** A search is not
   `(document, stage, component_version)`. Forcing a fake `target_id`
   either invents documents or overloads an unrelated kind.
4. **Assured operations share `QueryEngine`.** `OperationExecutor` calls
   `testimony_context` / `fact_context` / `resolve`. Metering `_embed`
   covers the cloud “recipe” spend path as well as `GET /search/*`.
5. **SQL open query is a second embed path.** The SQL executor does not
   go through `QueryEngine._embed`; it uses `selfhost_embed_query`.
   Cypher and SQL EXPLAIN do not call the provider. Closing only
   `_embed` would leave SQL nomination spend invisible.
6. **The published HTTP API has no meter export.** Issue #258 and the
   cloud pin inventory both record this. `remember ops` can already read
   the spine with the server extra; that is a local operator path, not a
   remote contract.
7. **Library boundary (D60/D61).** The engine must stay useful as a
   single-deployment self-host. Export and metering cannot exist only as a
   cloud sidecar. Cloud may *consume* the contract; it must not *be* the
   contract.

## 4. Alternatives for where to persist request-path spend

### A. Reuse `cost_ledger` via synthetic `processing_state` rows

Create a running processing row per search/operation, `record_call`, then
mark the row complete.

| For | Against |
| --- | --- |
| One table; existing budget SQL; issue #258’s “same CostMeterPort” reading | Requires a new `pipeline_stage` or a lie; `record_call` needs `running`; crash → stale-running (already blocks scale-to-zero recovery); unique `(target_kind, target_id, stage, component_version)` needs a fake target; workers must be taught never to claim the stage; budget windows start charging interactive embeds against worker ceilings unless we invent a lane |

**Reject.** The work ledger is a claimable queue. Interactive spend is not a
unit of work.

### B. Make `cost_ledger.processing_id` nullable and add request columns

One table, two identities.

| For | Against |
| --- | --- |
| Single export scan; self-hosters see one ledger | Weakens D67 (“attribution is copied from the locked running row”); budget SQL must filter nulls forever; every worker cost writer grows optional branches; a bug can write a worker call without a processing id |

**Reject.** Diluting the existing table so `processing_id` is optional
makes D67 “usually true” and lets a worker writer omit identity.

### B2. New unified table with an attribution discriminator

Exactly-one-of CHECK (processing attempt XOR request), partial unique
indexes, separate write methods.

| For | Against |
| --- | --- |
| One physical scan; honest if D67 is amended | Every budget SQL and D67 sentence grows a branch; existing `record_call` lock-and-copy no longer describes the table |

**Reject as the chosen shape.** Honest, not compelled. Two tables keep
worker code literally true. UMC source names are an **export**
vocabulary, not evidence for two physical tables.

### C. New `surface_cost_ledger` plus a union view (preferred)

A second append-only table for **request-path** provider calls. Worker
`cost_ledger` identity is unchanged. A view `v_cost_receipts` is the
operator read model so HTTP and CLI cannot omit a ledger.

| For | Against |
| --- | --- |
| D67 stays exact; no fake stages; request identity is not a processing id | Two physical tables; the view must stay the single read model |

`CostMeterPort` is already a bound sink and does not mention
`processing_id`. Reusing that protocol for a request-bound writer does
**not** recreate fake work. A distinct recorder type is constructor
clarity.

**Accept.** The extra table is the price of not lying about work identity.
The export contract is the single read model.

### D. In-process metrics only (Prometheus / logs)

| For | Against |
| --- | --- |
| No migration | Not durable; not replay-safe; not a receipt; cannot answer last Tuesday |

**Reject** as the system of record. Metrics may *also* exist.

## 5. Alternatives for export transport

The question is how an operator or supervisor reads receipts incrementally
without memory content.

| Option | Meaning | For | Against |
| --- | --- | --- | --- |
| **E1. Pull HTTP** on the engine | `GET /ops/cost-export` with a cursor | Versioned allowlist; testable; self-host remote-able; supervisor does not couple to engine migrations | Must not share the customer perimeter credential; cloud must not proxy it on the customer path |
| **E2. Push from engine to a supervisor URL** | Engine POSTs batches | Supervisor is simple | Engine would know a cloud URL and hold a cloud credential — the library becoming a cloud agent. Self-host has no supervisor. Retry/outbox is new engine machinery |
| **E3. Fleet job reads Postgres** | Supervisor SELECTs the tables | Fast for a packed host | No versioned allowlist; schema drift is the contract; self-host without DB access to a remote engine has nothing |
| **E4. CLI only** (`remember ops cost-export`) | Local server-extra dump | Trivial for the operator at the box | No remote contract; a supervisor that is not on the engine host cannot use it |

**E5. `memory_v1` relation.** The engine already has a versioned read
surface. Rejected: it sits behind the customer perimeter.

**Accept E1 as the remote contract**, on a **separate listener bind** so
the customer FastAPI app cannot grow the route (its `dependencies=` list
applies to every route on that app). **E4** is the same payload over a
local reader of `v_cost_receipts`. E3 is break-glass, not the contract.
E2 is rejected: the engine must not become a client of a commercial
control plane.

### Auth for E1

The customer perimeter (`AuthPerimeterPort` / `REMEMBERSTACK_API_AUTHORIZATION`)
is the credential a search client holds. Putting export behind that credential
expands blast radius: any token that can search can also dump every cost
receipt for the deployment.

**Accept a distinct export credential** on a **separate bind** (same
process, second uvicorn thread). If the bind is unset, HTTP export does
not exist on the customer port. The cloud supervisor holds the secret
on the fleet / ops side.

## 6. Receipt identity and cursor

Worker receipts already have `cost_id` (UUID). Surface receipts get their
own `cost_id`. The export stream is the union, each row carrying:

- `source`: `worker` | `surface` (engine names; a supervisor may map these
  to `engine_cost_ledger` / `engine_surface_meter`)
- `cost_id`
- `deployment_id`
- `work_id`: `processing_id` for worker rows; `request_id` for surface rows
- `stage` (worker) or `surface` (request path)
- `lane`, `attempt`, `call_key` when they exist
- `model_name`, `tokens_in`, `tokens_out`, `cost_usd`, `occurred_at`

**Forbidden on the wire and in export logs:** query text, vectors, prompts,
completions, chunk/claim/document bytes, filenames, retrieval snippets.

**Cursor.** Append-only is not enough (a row can commit after a later
row is already exported). Binding design: stamp `occurred_at` with
`clock_timestamp()` at INSERT on **both** ledgers; export only rows
older than a safety horizon; freeze that horizon on the cursor for
replay; **refresh** the horizon on every `next_cursor` while keeping
the last key so empty pages do not stall forever. See the design §5.2.

**Zero-cost rows.** Provider usage can theoretically report `0`. Persist
them (the call happened). A supervisor that requires `cost_usd > 0` for
its own settlement CHECKs may skip them; the engine must not drop them or
the “what did I spend?” question becomes incomplete.

## 7. Request identity on the API process

A single HTTP request can embed more than once (an assured operation runs
testimony then facts; each may embed). Those calls share one **request
id** so a supervisor can group them.

| Choice | Meaning |
| --- | --- |
| **F1. One UUID per inbound HTTP request** (preferred) | Middleware / surface entry sets a context; every embed in that request reuses it; `call_key` distinguishes embeds |
| **F2. One UUID per `_embed` call** | Simpler; loses “this recipe cost $X” without joining timestamps |
| **F3. Reuse cloud reservation id** | Couples the engine to a cloud header; self-host has none; D61 forbids making correctness depend on a control plane |

**Accept F1.** Library callers that are not HTTP (in-process tests, future
in-process MCP) open a request scope at the public `QueryEngine` method or
sandbox execute entry. If a scope is missing, the recorder mints one for
that call — never refuses the user-visible query because metering failed
to be wired at the edge.

Production composition **must** wire a durable recorder. A silent no-op in
`SelfHostProfile` is forbidden (startup assertion). Tests may inject a
recording fake.

## 8. Surface vocabulary

The request path is not one verb.

| Surface | Engine entries | Typical provider call |
| --- | --- | --- |
| `search` | `GET /search/claims`, `GET /search/chunks` | 1 embed when channel is semantic; 0 for BM25 |
| `operation` | `POST /operations/{name}` | testimony 2, fact 1, answer 3 typical embeds |
| `lookup` | `GET /lookup/observations?property_query=` | 1 embed |
| `open_query` | `POST /query/sql` **execute** | `selfhost_embed_query`; Cypher / SQL EXPLAIN: 0 |
| `library` | in-process `claims_about` / `claims_as_of` | 1 when ranking embeds |

`GET /resolve` does not embed. BM25-only search writes no row.

A supervisor that only settle-gates `search` and `recipe` maps
`surface=search` → `search` and `surface=operation` → `recipe`. Other
surfaces remain visible to the self-hoster and may be applied later.

## 9. Human `remember login` / `logout` (same program, separate PR)

This is not a metering problem. It is recorded here because the same
engine client must stop requiring humans to paste long-lived secrets.

The **cloud HTTP contract is already binding** (UMC device-grant v1 /
RFC 8628): `POST /v1/device/authorize`, `POST /v1/device/token`, bearer
`DELETE /v1/api-tokens/self`. The engine implements the **client**:

- display `user_code` + `verification_uri`
- poll at `interval` / honour `slow_down`
- store secret + non-secret ids in the client config directory,
  owner-only (`0600` file, `0700` dir)
- `logout` calls self-revoke then deletes the file
- precedence: `--token` / `--api-url` flags > `REMEMBERSTACK_*` env >
  config file

No parallel credential store. The minted token is a normal deployment
token. Self-host without a device-grant server still uses env/flags.

## 10. Preferred shape (input to the binding design)

1. Keep `cost_ledger` as the **worker** ledger. Do not null its
   `processing_id`. Do not enqueue search as work.
2. Add `surface_cost_ledger` for request-path provider calls.
3. Wire `QueryEngine._embed` and `selfhost_embed_query` (and any future
   request-path provider call in the API process) through a durable
   surface recorder.
4. Publish **pull** export `rememberstack.cost_export.v1` over HTTP
   (distinct credential) and the same payload via `remember ops
   cost-export` (local DB).
5. Implement `remember login` / `logout` against the existing device-grant
   contract.

## 11. Sources inspected (2026-08-13)

- `src/rememberstack/surfaces/query_engine.py` (`QueryEngine._embed`)
- `src/rememberstack/profiles/selfhost.py` (`selfhost_embed_query`)
- `src/rememberstack/workers/base.py` (`_LedgerCostMeter`)
- `src/rememberstack/spine/work_ledger.py` (`record_call`, `_INSERT_COST`)
- `src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py` (`cost_ledger`, `processing_state`)
- `src/rememberstack/model/queue.py` (`PipelineStage`)
- `src/rememberstack/surfaces/http_api.py`, `operation_executor.py`, `cli.py`, `sdk.py`
- `src/rememberstack/ports/cost_meter.py`, `ports/auth.py`
- GitHub issue #258
- UMC binding (external corpus, not in this repo): D42 Option R / D40
  device-grant — consumer constraints only.
