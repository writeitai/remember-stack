# Batch F — dual surface: open-query adapters, skill rewrite, freeze telemetry

> **Superseded for the shipping pre-release surface (2026-08-06).** This file
> records Batch F as built. D83 and
> `plan/designs/open_query_space_design.md` §8 remove the 17 compatibility
> adapters, their telemetry, and the noninferiority removal gate because no
> consumer exists to migrate. The nine open-query entry points, three assured
> operations, and 17 `examples.*` queries remain.

Batch F integrates the open query space as the dual surface alongside the
frozen legacy recipe adapters. It does not invent a second query engine,
parser, registry, RPC layer, or dynamic saved-query MCP tools.

## Problem

Batches A–E shipped SQL sandbox, Lance SRFs, graph helpers, full Cypher, and
the saved-query registry with all seventeen `examples.*` identities. Agents
still only reached those authorities through tests or private composition.
The consumption skill and OSS retrieval docs still steered recipe-first.
Default cutover requires a paid noninferiority gate that must not be started
from product code.

## Scope (smallest coherent dual surface)

1. **OpenQueryFacade** — one deployment-bound thin facade over
   `QuerySandboxExecutor`, optional `CypherSandboxExecutor`, manifest
   discovery, and the saved-query registry. Nine entry points exactly as
   design §3.1: `query_sql`, `explain_sql`, `query_cypher`, `explain_cypher`,
   `describe_query_space`, `search_query_space`, `list_saved_queries`,
   `describe_saved_query`, `run_saved_query`.
2. **Additive HTTP/SDK/CLI/MCP** — mounted only when the facade is composed;
   legacy `/recipes`, `/recipe/{name}`, `recipes()`/`run_recipe()`,
   `remember query list/run`, and recipe MCP tools remain behavior-frozen.
3. **Skill + OSS docs rewrite** — shared prose authority for the bound
   two-layer headline, three neutral choices, four bound SQL examples, and
   honesty warnings. Reference docs restore existing material and add
   open-query sections; they copy bound text and do not import Python.
4. **Migration-usage telemetry** — content-free counters of compatibility
   adapter vs open/core calls for the future <1% removal gate. No deprecation
   headers or warnings yet.
5. **Offline noninferiority machinery** — pure evaluator of already-collected
   arm metrics plus a paid-run estimate/plan command that never calls a model
   or starts a run.

## Authorities reused

| Concern | Authority |
|---|---|
| SQL parse/limits/execute | `QuerySandboxExecutor` (Batch B/C) |
| Cypher parse/limits/execute | `CypherSandboxExecutor` (Batch D) |
| Manifest discovery | `describe_query_space` / `search_query_space` (Batch B) |
| Saved identities | `SavedQueryRegistry` (Batch E) |
| Shipped examples | `examples.*` registry rows (Batch E seed) |
| Legacy recipes | `RecipeSurface` / registry (unchanged) |
| Headline / bound SQL examples | `core/open_query_prose.py` (single authority) |

## Discovery (first-call resource)

`QuerySpaceDescription` exposes every already-loaded authoritative field:
views, functions, `limits` (every named field of each tier record),
`core_operation_descriptors`, `function_signatures`, `sql_grammar`,
`cypher_dialect`, `p2_projection`, plus the first-call members required by §6:
`retrieval_choices`, `honesty_warnings`, and structured `worked_examples`.

HTTP and local MCP share one serializer
(`query_space_description_payload` via `dataclasses.asdict`) so neither
hand-selects a shortened subset that can drift. Remote SDK forwards the HTTP
payload unchanged.

Worked examples (exactly eight) include the bound wrong/right contrast pair,
predicate vocabulary, full audit trail, latest-contradicting testimony,
snapshot-ID-to-live SQL, native Cypher traversal/aggregation, and
semantic-to-relational. Core owns the native Cypher body
(`NATIVE_CYPHER_TRAVERSAL_AGGREGATION`) and the `examples.claims_verbatim`
purpose/SQL. The examples registry imports the claims constants so skill and
seed cannot drift. The hashed limits manifest keeps its Batch E
`query_cypher` node-list example and surface hash unchanged — rolling the
hash solely for first-call prose would fail closed against existing
`saved_query_registry_state` pins and requires the Batch E pending-revalidation
protocol, which is out of scope for this batch.

`search_query_space` returns typed `DiscoveryHit` rows across views, function
signatures, core operations, and shipped example names/purposes. Scoring stays
deterministic; `k` in 1..25; no `pg_catalog` or tenant content.

## Saved-query execution defaults

`SavedQueryRegistry.resolve` accepts an optional exact `version`. When set,
that version must exist and be `active` (pending/disabled/broken/not-found
keep the existing typed codes). `SavedQueryVersion` carries `default_limits`.

`run_saved_query` resolves, then executes stored SQL through the **same** SQL
executor with bound positional parameters. Stored `max_rows`,
`statement_timeout_ms`, and `max_bytes` all apply via a small internal
executor override seam and are clamped to the selected tier hard caps. A
caller-provided `max_rows` wins over the stored default. There is no second
execution path and no name-to-`$n` parameter mapping — bound positional
parameters and sandbox cardinality/type failures remain the authority.
Results are never cached; `QueryResult.saved_query` is stamped with the
design §4.4 shape `query_id` / `namespace` / `name` / `version` /
`query_hash` (string-valued fields).

## Routes and tools

HTTP (when `open_query=` is composed on `build_api`):

| Method | Path |
|---|---|
| POST | `/query/sql` |
| POST | `/query/sql/explain` |
| POST | `/query/cypher` |
| POST | `/query/cypher/explain` |
| GET | `/query/space` |
| GET | `/query/space/search` |
| GET | `/query/saved` |
| GET | `/query/saved/{namespace}/{name}` |
| POST | `/query/saved/{namespace}/{name}/run` |

MCP static infrastructure tools are named exactly after the nine facade
operations. `tools/list` = recipe tools + those nine. `examples.*` are never
tools. Local (`RecipeMcpServer`) and remote (`RemoteRecipeMcpServer`) share
`mcp_tools.py` schemas, strict argument validation, and dispatch.

CLI keeps `remember query list` / `run` for recipes and adds
`sql`, `explain-sql`, `cypher`, `explain-cypher`, `space`, `search-space`,
`list-saved`, `describe-saved`, `run-saved`. Offline gate:
`remember eval open-query-gate` (`--estimate` or `--metrics`).

Self-host `api()` wires SQL (query-role connect), full Cypher
(`GraphSnapshotReader`), discovery, and saved-query reads/runs. Query-role
password provisioning is **deploy-time setup only** (not API request/startup
composition). D68 physical routing plus grants remains the tenancy model; no
PostgreSQL RLS.

## Legacy freeze

During introduction:

- All 20 compatibility recipes remain callable with frozen versions, input
  schemas, and Envelope contracts.
- No deprecation headers, SDK warnings, CLI stderr deprecation, or MCP
  deprecation descriptions are emitted — default cutover has not passed the
  paid noninferiority gate.
- The three assured operations remain the only platform intent operations.

## Telemetry

`MigrationUsageCounters` records only surface class
(`compatibility_adapter` / `open_query` / `core_operation`) and operation
name. Arguments, SQL/Cypher text, rows, and bodies never enter the counter.
The §8 open-query denominator counts only retrieval-bearing calls
(`query_sql`, `query_cypher`, `run_saved_query`); explain/discovery/list/
describe do not increment it. Recipe core and compatibility runs remain
retrieval calls. Counters default to disabled unless the host injects an
enabled recorder (self-host injects a shared enabled instance from `api()`).

## Validations

Focused unit/adapter tests cover:

- facade SQL + full discovery payload + search hits + run_saved_query stamps
- stored default limits (max_rows, statement_timeout_ms, max_bytes) on QueryResult
- mismatched deployment composition via public `deployment_id` properties
- HTTP open routes and typed sandbox mapping; legacy `/recipes` still lists
- MCP lists nine open tools + recipes, not `examples.*`
- strict local and remote MCP argument validation
- skill opens with the bound headline and the same worked-example set as discovery
- telemetry privacy
- offline noninferiority gates and estimate arithmetic

No paid benchmark, OpenRouter call, or network-billed evaluation is run as
part of this batch.

## Explicit unpaid benchmark blocker

The §8 hybrid noninferiority gate for **default cutover** requires a
same-condition paid hybrid arm. Product code only evaluates already-collected
metrics offline and estimates case/arm/call cost from operator-supplied unit
costs. The overall criterion is the already-collected lower 95% confidence
bound of the open-vs-legacy success delta (`success_delta_lower_95` on the
open arm), compared directly to -2 absolute points — product code does not
invent or recompute a CI from insufficient aggregates. **The real paid run
remains operator-gated and is not started by this batch.** A v10 open-only
protocol is an explicit design deferral (§10) and is not invented here.

## Limitations

- Cypher without a published P2 snapshot fails `p2_unavailable` (existing
  executor contract).
- Self-host query-role password reuse is deploy-time provisioning, not a
  multi-tenant secret scheme and not request-path role alteration.
- No migration was required.
- Consumption skill version is `2.0.0`.
- Parameter schemas describe types/cardinality only; there is no name-to-`$n`
  mapping in the accepted design.

## Files (primary)

- `core/open_query_prose.py` (shared headline/examples authority)
- `surfaces/query_sandbox/open_query.py`, `mcp_tools.py`, `discovery.py`
- `surfaces/query_sandbox/executor.py` (limit override seam)
- `surfaces/query_sandbox/saved_queries.py` (exact-version resolve + public id)
- `surfaces/query_sandbox/audit.py` (usage counters)
- `surfaces/http_api.py`, `sdk.py`, `cli.py`, `mcp.py`, `remote_mcp.py`
- `surfaces/recipe_surface.py` (usage recording only)
- `core/consumption_skill.py`
- `eval/open_query_noninferiority.py`
- `profiles/selfhost.py`
- website reference/concepts/mounts/status pages
- `tests/surfaces/test_open_query_batch_f.py`
