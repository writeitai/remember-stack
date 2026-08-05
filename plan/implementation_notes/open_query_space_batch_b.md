# Batch B — the SQL sandbox: what shipped, and what did not

The sandbox is the execution environment between an agent's SQL and
PostgreSQL: a parse gate, an unprivileged deployment role, per-request caps,
typed parameters, and a provenance contract. §4.2's row-level security and
`security_barrier` are deliberately absent (operator decision, 2026-08-04);
tenancy is physical routing plus grants.

## The tenancy boundary is per deployment, not per cluster

Roles are cluster-global objects while D68 gives each deployment its own
database, and PostgreSQL grants `CONNECT` on every database to `PUBLIC`. A
single shared `rememberstack_query` login would therefore reach every
deployment's database in the cluster and accumulate each migration's grants —
which is what a review found. The login is derived from the database name
(`rememberstack_query_<database>`), the database withdraws the `PUBLIC`
defaults (connect, temp, schema usage, function execute, large-object APIs),
and `CONNECT` is granted back to exactly one role. A test creates a second
deployment database and proves the first deployment's login cannot enter it.

## Recorded gaps (follow-ups, not silent omissions)

Reviews confirmed these remain outside what this batch implements. They are
listed so a later batch closes them deliberately rather than discovering them:

1. **Structural grammar rules are code, not manifest members.** The hash now
   covers the allowlists, the public function names, the invocation and
   recursion bounds, and every tier cap — so those changes roll the surface
   identity. The *shape* rules (SRF placement, the recursive template, CTE
   shadowing, the rejection mapping) are not yet expressible as manifest data
   and can change without rolling the hash. Closing this means giving the
   rules a declarative form; it is not a one-line addition.
2. **`QueryResult` fields awaiting later batches**: `referenced_graph_types`,
   `referenced_graph_properties`, and `confirmation` belong to the Cypher and
   semantic surfaces (Batches C and D) and are absent until those land.
3. **`exact_total_known` is never true.** Recognizing an outer exact-count
   query requires AST analysis the gate does not yet do; the contract's
   default (unknown) is the honest answer meanwhile.
4. **Column nullability is always reported as `true`.** PostgreSQL does not
   report result nullability for computed columns, and inferring it per
   expression would be a guess; the contract says what it knows.
5. **The byte count is a JSON-encoded row estimate**, not the wire payload of
   the transport the caller eventually uses. It bounds the same quantity the
   cap bounds, and both sides use the same measure.
6. **`§4.3` caps not modeled as separate default/hard members**: the
   `TierLimits` dataclass carries defaults and hard caps as sibling fields
   rather than the design's two-column table shape. Every field is hashed;
   the *presentation* differs from the design's table.

## Verification

Four gates on every commit: the full suite (`-p no:randomly`; the module-scoped
database fixtures are order-sensitive), `pyright`, `ruff check`, and
`ruff format --check`.
