# Proposal: `data_query` over normalized copies of data files

**Status:** not chosen (2026-09-27, D138). Designed as part of D133 §4.6 and reviewed there;
removed from the binding design when D138 settled that coding agents compute on the original
files themselves.

## Adoption trigger

Adopt when agents **without file access** (for example an MCP client in a hosted chat product that
cannot open the deployment's files) repeatedly need row-level answers from ingested data files, and
the D138 profile plus `source_open` cannot serve them.

## The design as reviewed

The text below is the D133 design as it stood when removed. Section numbers refer to D133.

### The queryable copy and `data_query`

At conversion time a profiler writes each table it profiled as a normalized
Parquet file: sheets, CSV, database tables, JSON arrays of records and
parsed log lines. These are **private query assets**, stored in the
deployment's **private store** — a third object-store root beside raw and
artifacts (`e0_files_design.md` §2) that no mount, P3 projection or `hydrate`
depth ever reads — at `<doc_id>/<content_hash>/<representation_id>/<table>.parquet`.
Only the `data_query` worker's staging step reads it. It is purged with the
representation and inventoried by hard forget (§5.4). The manifest lists the
assets with hashes and table names. Staged container members (§5.1) live in
the same store. Parquet gives one typed layout for every
format; the cost is a second stored copy, accepted because the alternative
re-parses the original on every query.

**`data_query` is a direct retrieval primitive**, bound in
[`retrieval_design.md`](../designs/retrieval_design.md) §3:

```
data_query(version_id, representation_id?, sql, params?) → envelope (evidence grain) carrying DataQueryResult/v1
```

- **What it runs:** one read-only SQL statement in DuckDB (an in-process
  analytical database) over that representation's tables, exposed as views
  named by table.
- **Isolation.** DuckDB's own guidance is that its settings are defence in
  depth and untrusted SQL needs a sandbox
  (<https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview>,
  retrieved 2026-09-23). So:
  - each query runs in a **separate worker process** with no credentials and
    **no network**: the process runs in an OS network sandbox with no
    interfaces (a network namespace, or a container started without a
    network). A deployment platform that cannot provide one does not enable
    `data_query`; the primitive then answers with a typed `boundary`, never
    with a weaker sandbox. OS resource limits cap CPU time, address space,
    open files and file size;
  - the parent stages copies of only the needed Parquet files into a
    per-query **input directory** the worker can read but not write, and
    creates a separate, empty per-query **scratch directory** for DuckDB's
    temporary files;
  - DuckDB is configured before any agent SQL: `allowed_directories` = the
    input directory, `enable_external_access=false`,
    `autoinstall_known_extensions=false`, `autoload_known_extensions=false`,
    `allow_community_extensions=false`, `temp_directory` = the scratch
    directory with `max_temp_directory_size`, `memory_limit`, `threads`, then
    `lock_configuration=true`;
  - the parent enforces **wall time** by killing the process and cancels on
    caller disconnect; both directories are deleted afterwards.
  - Starting limits: 10 seconds wall time, 1 GiB memory, 2 threads, 1 GiB
    temporary disk, 1,000 returned rows.
- **Result: `DataQueryResult/v1`**, its own contract — not the open query
  space's `QueryResult/v1`, which is bound to PostgreSQL and the `memory_v1`
  schema. Fields: `contract = "DataQueryResult/v1"`; `statement_sha256` (of
  the SQL text exactly as received) and `params`; `columns[]` with name,
  DuckDB type and a portable type (`integer`, `decimal`, `float`, `text`,
  `boolean`, `date`, `timestamp`, `binary`, `nested`); `rows[]`;
  `row_count`; `truncated`; `elapsed_ms`; and `source` = document version,
  representation, and each table read with its asset hash. The D49 envelope
  carries it as the payload of its single evidence item — `data_query`'s own
  envelope binding, not a generic result adapter. Errors are typed:
  `invalid_sql`, `write_rejected` (anything other than one read-only query),
  `timeout`, `resource_limit`, `invalid_parameter`, and `boundary` when the
  document has no query assets (not a profiled family, or conversion
  incomplete) or the platform cannot sandbox the worker.
- **Authorization and audit:** the same authorization path as
  `hydrate depth=bytes` and `source_open`; every call audits principal,
  document version, statement hash and rows returned.
- **Surfaces:** HTTP API, SDK, CLI and MCP (retrieval §7). No mount
  equivalent: mounted agents already have the original on the raw mount.

