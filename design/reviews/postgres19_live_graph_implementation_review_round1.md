# PostgreSQL 19 live-graph implementation review — round 1

**Status:** non-binding review evidence; fixes are tracked against the accepted
design and D98. **Reviewed:** 2026-08-27.

## Review execution

The independent Claude review was run from the OSS repository with the exact
required CLI shape:

```text
claude --model opus --dangerously-skip-permissions --print "<implementation-review prompt>"
```

It completed with verdict `CHANGES_REQUESTED`. The required Antigravity command
was also attempted exactly:

```text
antigravity --model gemini-3-pro-high --yolo --prompt "<implementation-review prompt>"
```

That command could not run because `antigravity` is not installed on `PATH`.
The macOS application bundle is present, but its application binary is not the
requested CLI. This review record does not substitute another Gemini command
or claim a second independent review.

## Findings and disposition

| Severity | Finding | Disposition |
| --- | --- | --- |
| Blocker | One-hop SQL/PGQ could return a self-loop. | Fixed: the one-hop pattern excludes `y.entity_id = x.entity_id`; static and database parity tests cover the fixed shape. |
| Blocker | The SQL/PGQ degree guard did not bound the work it claimed to count. | Fixed: shallow SQL/PGQ first materializes the canonical bounded neighborhood helper and runs `GRAPH_TABLE` only when expansion/frontier/time admission succeeds. |
| Blocker | Citation depth was 12 in implementation but 6 in the accepted contract. | Fixed: helper, OSS HTTP/SDK, managed proxy, docs, and tests hard-clamp at 6. |
| High | Temporal SQL/PGQ was derived by unchecked textual replacement. | Fixed: exact-marker replacement asserts the expected occurrence count at import time; tests pin the current/history substitutions. |
| High | Graph work shared the ordinary pool/role without a separate concurrency boundary. | Fixed: a graph role, exact role limits, separate SQLAlchemy pool, bounded semaphore, short wait, 5-second statement timeout, read-only repeatable-read transactions, and graph `work_mem` are explicit. |
| High | Managed host capacity assumed homogeneous deployment shapes and was not tied to actual residency. | Fixed in UMC: capacity evidence lists every deployment reservation, exact host id/resident ids, and sums heterogeneous memory, connections, and expansion concurrency; reservation is restricted to the reviewed host. |
| High | Managed rendering retained a local build path beside the immutable PostgreSQL digest. | Fixed in UMC: digest validation requires a full repository coordinate and lowercase SHA-256; rendered deployment YAML strips all build stanzas and is idempotent. |
| High | Cross-client graph exposure was not explicit. | Fixed in binding design: typed graph is deliberately HTTP/SDK in D98; CLI/MCP keep their existing general recall/ingest contracts and do not imply dedicated graph verbs. |
| High | Acceptance coverage was too thin. | Fixed/expanded: shallow PGQ/helper parity, over-budget behavior, catalog repair/ACL/role smoke, typed HTTP, SDK, compose, capacity, and readiness proofs are executable; the complete PG19 database run remains an explicit release gate. |
| High | Readiness did a full catalog audit on every request and collapsed failure causes. | Fixed: semantic catalog verification is cached briefly and produces typed non-secret server/extension/role/helper/catalog/execution reasons; absent-anchor probes retry collisions. |
| Medium | All SQLSTATE `22023` failures were mapped to graph-clock errors. | Fixed: only exact errors from graph helpers receive that mapping. |
| Medium | Hydration silently dropped paths when authority rows did not match. | Fixed: hydration fails closed and HTTP sanitizes the internal mismatch as a 503. |
| Medium | Open-query graph helpers inherited the 60-second analytical timeout. | Fixed: graph-helper calls are capped at 5 seconds. |
| Medium | Graph routes were not spend-gated without an explicit rationale. | Fixed in UMC billing design: provider-free graph reads are rate- and graph-capacity-admitted but do not reserve provider spend. |
| Medium | Candidate compatibility/version/removal semantics could look released. | Fixed in the managed profile: activation is explicitly unreleased/inactive, merge/release coordinates are pending, and removed recipe routes are recorded. |
| Medium | FE global vocabulary could apply “projection delayed” to live graph. | Fixed in UMC FE design: the state is explicitly P3 structure projection delay and excluded from graph UI. |
| Medium | Reserved-identifier scanning omitted `RangeVar.relname`. | Fixed. |
| Medium | Compose forbidden-material scanning omitted top-level YAML. | Fixed: the complete rendered document is scanned. |
| Low | Predicate arrays bounded item count but not item length; status reasons and catalog repairability were loose. | Fixed: predicates are non-empty and length-bounded, terminal status is internally consistent, and environment incompatibility is not replayed as catalog repair. |

## Remaining gates

- Run the full database suite against a healthy PostgreSQL 19 Beta 3 image with
  the exact extensions; the prior Docker overlay filesystem developed an I/O
  failure, so a fresh database proof is still mandatory.
- Build and pin real multi-architecture application/PostgreSQL images; UMC must
  pin the linux/amd64 digests used on Hetzner.
- Run round 2 after validation.
- Run the required Antigravity review when the requested CLI is available.

