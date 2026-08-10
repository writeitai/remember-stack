# Batch D — graph surface implementation

> **Historical implementation note.** The graph and Cypher work remains built.
> D87 removes the `question_context` v4 catalog contract described here; the
> current binding assured-operation catalog is
> `open_query_space_design.md` §3.1. Do not generate an alias or active tool from
> the legacy sections below.

Batch D adds the PostgreSQL graph helpers, the read-only Cypher surface, and
`question_context` v4. The binding contract is
`plan/designs/open_query_space_design.md`; the reconciliation rationale is in
`plan/analysis/open_query_space_batch_d_reconciliation.md`.

## PostgreSQL helpers read the same graph as direct SQL

`memory_v1.graph_neighborhood` and `memory_v1.graph_path` traverse
`graph_edges_current` when neither instant is supplied. When a caller supplies
time, both `valid_at` and `believed_at` are required and the helpers traverse
`graph_edges_visible_history` with half-open D41 intervals. Null endpoints are
open intervals.

Traversal is undirected but every returned edge keeps its stored subject and
object. A branch never reuses a relation or revisits an entity. The helpers
clamp depth, path, and edge limits internally, order before cutting, and spend
the path edge cap on whole paths so a partial route is never returned as a
connection. Current helper rows disclose `statement_timestamp()`, the instant
that selected them, rather than the transaction start. Their live-at-read
support fields retain the `_current` suffix even on historical traversal rows,
so a caller cannot mistake those values for a historical reconstruction. The
helpers stay `SECURITY INVOKER`; they do not inherit the projection bridge's
elevated owner. Their transaction-local cap marker makes them `PARALLEL
UNSAFE`, while the query role already disables parallel workers.

After stacking Batch A, the graph migration is `p9_05_0026` and depends on
Batch A's `p9_04_0025`. The graph edge views express endpoint membership as
`EXISTS` semijoins because they publish no entity columns; this is equivalent
to the prior joins without multiplying the corrected `entities_current`
authorization plan. The helpers preserve their written join order and
materialize the bounded edge input once, preventing PostgreSQL plan search
from exhausting memory without changing which rows are visible.

## The Cypher gate stays lexical

The pinned LadybugDB engine is the Cypher syntax authority. The pre-engine gate
does only the two jobs it can do exactly without rebuilding a parser:

- it default-denies the statement kind by accepting only `MATCH`, `OPTIONAL`,
  `WITH`, `UNWIND`, or `RETURN` as the first unquoted token; and
- it rejects the pinned external-action, extension, session, maintenance,
  attachment, and plan-control tokens wherever they occur outside strings,
  quoted identifiers, and the engine's `//` or `/* */` comments; and
- it rejects the observed physical-address function family — `id`, `rowid`,
  `internal_id`, `offset`, `hash`, `cast`, `string`, and `to_string` — including
  spacing/comment and backtick-quoted call variants.

One statement and 32 KiB of Cypher text remain hard request bounds. The scan
does not treat `--` as a comment because the pinned engine does not.

Mutations are not guessed from text. The snapshot opens with
`Database(..., read_only=True)`, whose exact mutation refusal is pinned by a
live canary and mapped to `cypher_not_allowed`. This division is important:
file/extension actions that read-only does not block die in the lexical gate,
while the engine enforces its own write grammar.

The engine's 30-hop recursive ceiling is engine-native. Timeout, row, and byte
caps are separate executor resource bounds; failure to install the timeout
fails before execution with a content-free engine fault class. The removed
hop/bracket/reference walker is not reintroduced.

## No nominal worker boundary

The earlier accepted design named a process-isolated worker with filesystem
and network confinement. This repository has no portable host sandbox that
provides those controls. Spawning an ordinary Python process would add an RPC
and snapshot-path seam while still permitting the access the contract claimed
to prevent.

The binding design now records the narrower implementation honestly: the
deployment-bound reader selects one snapshot server-side and opens it
read-only; the lexical gate prevents the external-action family. The observed
LadybugDB INT128 failure raises rather than hanging or corrupting the API
process. Real process confinement is reopened only when the hosting layer can
supply it or observed engine behavior requires a fault boundary.

## Snapshot provenance is pinned to the connection

The graph rebuild records the export transaction's own
`transaction_timestamp()` as `built_at`. The reader returns its connection and
generation provenance under the same refresh lock, preventing rows from one
generation being labelled with another generation's cut. No published
snapshot is `p2_unavailable`; it is not reported as an empty graph.

Each Cypher request leases a distinct read-only connection under that lock and
closes it after execution. The connection-local timeout therefore belongs only
to the request whose disclosed tier selected it; concurrent interactive and
analytical requests cannot overwrite each other's bounds.

Every Cypher result has grade `snapshot_graph`, no SQL schema name, and the
snapshot ID/version/build instant/age that actually served it. Engine physical
offsets (`_ID`, `_SRC`, `_DST`, matched case-insensitively) are stripped from
structural values, and a scalar engine `INTERNAL_ID` result is refused rather
than publishing its physical `{offset, table}` address. The pinned functions
observed to expose, derive, or erase the type of an address are refused before
execution; ordinary public `e.id` and ``e.`id` `` properties remain available.
Snapshot identity is
pinned with the connection, failures after pinning retain that identity,
snapshot age is measured at execution start and clamped nonnegative, and an
age over 3600 seconds emits the bound freshness warning.

The engine exposes no structural parse metadata for exact graph label/property
dependency extraction. `referenced_graph_types` and
`referenced_graph_properties` are therefore null, meaning unavailable, rather
than empty arrays that would assert a known-empty dependency set.

## `confirm=true` is explicit and narrow

Confirmation defaults false. When requested, it checks unique IDs carried by
top-level engine-typed `NODE`/`REL` values labelled `Entity` and `RELATES` in one PostgreSQL
repeatable-read transaction and drops a row if any recognized ID is no longer
live. `Document`, `MENTIONED_IN`, `DOC_CROSSREF`, aggregates, collections, and
scalar UUID projections stay snapshot-scoped. A caller-authored struct carrying
the same label keys is not a typed node or relationship and is not nominated.
Inferring authority from a column name, UUID shape, or forgeable map content
would misclassify values and is forbidden.

Cypher and SQL share the same kill-switch, concurrency, rolling-quota, and
content-free audit components when composed for a deployment. Admission wraps
snapshot execution and optional confirmation; every admitted attempt releases
its slot and records its spend on success or failure.

The result always retains grade `snapshot_graph`. It reports requested,
nominated, confirmed, dropped-stale, and the PostgreSQL confirmation instant.
If no structural value was confirmable it still checks PostgreSQL availability,
reports the required zeros, and warns that nothing in the result was checked.

## `question_context` v4 reuses existing authorities

The default answer remains hybrid claims plus hybrid live source chunks.
`include_facts` and `include_entities` both default false and work independently
or together:

- facts reuse `current_context`'s semantic nomination, PostgreSQL current-fact
  confirmation, both evidence stances, fixed evidence depth 3, 30-fact ceiling,
  and 60-association budget; and
- entities take exact resolution candidates first, then semantic description
  nominations, admit at most 21 deterministic candidates from each channel,
  deduplicate by survivor ID, confirm the full bounded combined set once
  through `memory_v1.entities_current`, and then return at most 20 live
  survivors. Standalone `resolve` remains exhaustive.

Claims, chunks, facts, fact/evidence links, totals, and entities remain in their
existing typed Envelope fields. `current_context` stays v1. Graph expansion is
not silently added to either operation; the caller-visible
`multi_hop_context` owns that behavior.

The canonical recipe and public descriptor are v4. The checked-in manifest now
contains all three assured-operation descriptors plus the PostgreSQL graph
helper and Cypher entry-point signatures, so the tool catalog and
`surface_manifest_hash` roll atomically.

## Verification

Focused verification covers:

- helper bounds, clocks, undirected traversal, whole-path cutting, and live
  visibility;
- gate placement attacks, read-only mutation mapping, parameter binding,
  timeout/row/byte disclosure, mixed-tier timeout isolation, and snapshot
  provenance;
- structural confirmation, no-confirmable warnings, stale-row dropping, and
  nullable graph-reference metadata;
- both v4 flags default-false, independently enabled, and enabled together;
- v4 fact/entity confirmation, channel caps, and high-fanout exact aliases; and
- descriptor, function-signature, and manifest-hash determinism.

Broad supported-Python suites run in CI. The known LadybugDB INT128 traversal
flake is rerun when it appears; it is not treated as evidence for unrelated
code changes.

The final resource/provenance review also pins the installed Ladybug dependency
to the manifest's exact gated version, closes a request lease when its snapshot
provenance fails validation, and retains the export cut on an out-of-order
snapshot recorded as superseded. These are lifecycle and disclosure fixes; no
new parser, isolation layer, or retention behavior was added.

## Final review corrections

Current-context and graph-context evidence hydration now join
`fact_claim_evidence_live`, `evidence_lineage`, `claims_live`, and
`documents_live`; one document lineage counts once and a missing or tombstoned
document cannot authorize evidence. The Batch D retrieval fixture carries
honest surviving provenance for withdrawn and isolated cases.

`multi_hop_context` materializes its PostgreSQL-confirmed edge set once and
preserves the hydration query's written join order with transaction-local
planner settings. After the graph view has authorized both endpoints, base
entity rows supply names and types only. The same planner settings bound the
SQL sandbox's expansion of the invariant-compiled views. The P2 export keeps
the same authoritative graph view and repeatable-read cut, while disabling JIT
and parallel workers in that transaction so compiling the deep view cannot
exhaust the database before its server-side cursor returns.

Nested LadybugDB `INTERNAL_ID` logical types and the observed physical-address
function family are refused, while caller-authored field names and public `.id`
properties remain data. Timeout installation fails closed. Failures after
snapshot pinning retain the stale-age warning, and audit events carry snapshot
freshness, confirmation counts, graph caps, and a content-free engine fault
class.

The graph migration inlines the paired-clock error in the two public helpers,
revokes PUBLIC function execution and its schema default, and grants the routed
query role only documented functions. Its comments and manifest examples now
state the both-or-neither clock rule and `invalid_parameter_value` failure. The
P2 deletion target is no longer
deferred: the SQL matrix executes it and a worker fixture proves that the edge
remains only in the old disclosed snapshot and disappears after rebuild. The
manifest and OSS API reference now publish the actual Cypher openings,
engine-rejected mutations, 32 KiB text cap, v4 flags, and snapshot semantics.

## Graph-helper cap disclosure

The PostgreSQL graph helpers now set one transaction-local marker when their
depth, edge, or path bound omits reachable work. The SQL executor resets and
reads that marker around the already-materialized helper invocation, then sets
`QueryResult.truncated`, `truncation_reason = "graph_cap"`, and a warning. This
also covers a whole path omitted by an edge budget, where the helper returns no
public row from which the executor could otherwise infer the cut.

The marker carries no corpus data and is scoped to the existing request
transaction. It adds no query, parser, role, RLS policy, or second traversal.

Cypher query hashes now use the existing scanner's token sequence with
formatting and real engine comments removed, plus canonical LadybugDB logical
parameter families. Different values of one logical type hash the same, while
different logical types do not. This meets the audit identity contract without
adding the parser that D82 rejects.

## Post-review CI corrections

The D48 artifact now marks `public.v_memory_fact_visible` applicable to the
P2-edge deletion target; the executable matrix had proved the fact identifier
was reachable there before deletion, so calling that cell inapplicable was an
artifact error.

`current_context` applies the same transaction-local planner bounds as the
other coordinate-complete hydration paths. Its live evidence views expand a
deep authorization tree, and leaving PostgreSQL free to reorder that tree can
exhaust the server before the bounded query returns. This changes no membership
or public result contract.

Finally, LoCoMo full-v9 retains its exact v3 `question_context` descriptor and
catalog hash. The live recipe is v4, but silently rolling a prepared v9
protocol would make old and new runs look comparable. A later manifest-pinned
protocol remains a distinct Batch F identity.

## Final discovery and snapshot-contract corrections

Each P2 artifact manifest now carries both the SHA-256 of the exhaustive
projection schema and the current `surface_manifest_hash`. A reader checks both
pins before opening either downloaded or previously cached bytes. Missing and
stale pins fail as `schema_version_mismatch`, so a snapshot built by the earlier
exporter cannot inherit the current surface identity.

`describe_query_space` now returns the checked-in manifest's exact assured
operation descriptors, full function signatures (including both Cypher entry
points), Cypher dialect, and P2 projection contract. It retains the compact SQL
name list for compatibility, but no longer reconstructs a smaller contract
from parallel constants.

The manifest's assured-operation input schemas now come from the same public
descriptor renderer used by API, CLI, and MCP. Internal recipe flags therefore
cannot leak into an invalid or different JSON Schema. These corrections reuse
the existing manifest and descriptor authorities; they add no new registry,
parser, RLS policy, or discovery model.
