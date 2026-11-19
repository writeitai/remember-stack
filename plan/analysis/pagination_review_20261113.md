# D154 review corrections — 2026-11-13

The accepted review of draft PR #589 found that a stable cursor position alone
does not guarantee a stable listing. This analysis explains the corrections
to D154; the decision and affected binding designs carry the current contract.

- **Belief visibility:** a declaration can be stamped before page one's clock
  yet commit after its snapshot. A repeatable-read transaction only freezes one
  request. Lookup browse therefore uses the existing deployment belief guard
  and watermark before opening its snapshot, just as the other scoped document
  reads do. Persisting snapshots or introducing a second clock is unnecessary.
- **Deletion:** a graph offset changes meaning when a returned edge disappears.
  Resume after the last `(hops, relation_ids, node_ids)` tuple in the helper's
  deterministic order instead. One-hop reads already materialize an admitted,
  bounded neighborhood; deeper reads use the existing 500-row helper ceiling
  and filter that bounded result by the tuple. No migration, new helper or
  unbounded expansion is needed. The HTTP cursor bound is 2,048 characters,
  enough for the four-hop ordering tuple and clocks. Removed edges cannot be reconstructed; surviving
  paths retain their order and the terminal status discloses any work ceiling.
- **Scope:** filters alone identify the same listing in two different projects.
  The shared browse-scope hash now requires the deployment UUID as well as the
  listing's filters, IDs and time selectors. Page size remains outside the hash.
  Foreign cursors are refused before listing SQL. Cost export is exempt: its
  existing resumable billing feed, encoding and bytes remain unchanged.
- **End detection:** filling a reference scan budget does not prove another
  candidate exists. Peek at one candidate in each remaining direction before
  returning the cap cursor. This is bounded by two directions and avoids a
  corpus count or further reference expansion.
- **Surface validation:** Python MCP enforces the advertised SQL maximum;
  TypeScript classifies browse `limit` as a top-level search argument; CLI
  rejects query plus cursor before opening its client. Inventory checks JSON
  field types before constructing timestamps and UUIDs, so malformed data takes
  the existing cursor-error path rather than producing an attribute error.

The source documentation tables and examples must describe `limit` for browse,
`next_cursor` for responses, and refusal above the SQL maximum. Fresh generated
contracts and llms files cannot detect stale prose by themselves, so regressions
execute the published paging loop and check the affected input tables. These
changes affect reads and argument validation only; no ingestion identity or paid
reuse boundary changes.
