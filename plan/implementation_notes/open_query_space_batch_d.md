# Batch D — graph surface implementation

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
that selected them, rather than the transaction start.

## The Cypher gate stays lexical

The pinned LadybugDB engine is the Cypher syntax authority. The pre-engine gate
does only the two jobs it can do exactly without rebuilding a parser:

- it default-denies the statement kind by accepting only `MATCH`, `OPTIONAL`,
  `WITH`, `UNWIND`, or `RETURN` as the first unquoted token; and
- it rejects the pinned external-action, extension, session, maintenance,
  attachment, and plan-control tokens wherever they occur outside strings,
  quoted identifiers, and the engine's `//` or `/* */` comments.

One statement and 32 KiB of Cypher text remain hard request bounds. The scan
does not treat `--` as a comment because the pinned engine does not.

Mutations are not guessed from text. The snapshot opens with
`Database(..., read_only=True)`, whose exact mutation refusal is pinned by a
live canary and mapped to `cypher_not_allowed`. This division is important:
file/extension actions that read-only does not block die in the lexical gate,
while the engine enforces its own write grammar.

The engine's 30-hop recursive ceiling is engine-native. Timeout, row, and byte
caps are separate executor resource bounds. The removed hop/bracket/reference
walker is not reintroduced.

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

Every Cypher result has grade `snapshot_graph`, no SQL schema name, and the
snapshot ID/version/build instant/age that actually served it. Engine physical
offsets (`_ID`, `_SRC`, `_DST`, matched case-insensitively) are stripped from
structural values.

The engine exposes no structural parse metadata for exact graph label/property
dependency extraction. `referenced_graph_types` and
`referenced_graph_properties` are therefore null, meaning unavailable, rather
than empty arrays that would assert a known-empty dependency set.

## `confirm=true` is explicit and narrow

Confirmation defaults false. When requested, it checks unique IDs carried by
top-level structural `Entity` and `RELATES` values in one PostgreSQL
repeatable-read transaction and drops a row if any recognized ID is no longer
live. `Document`, `MENTIONED_IN`, `DOC_CROSSREF`, aggregates, collections, and
scalar UUID projections stay snapshot-scoped. Inferring authority from a
column name or UUID shape would misclassify values and is forbidden.

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
  nominations, deduplicate by survivor ID, confirm the combined set once
  through `memory_v1.entities_current`, and return at most 20.

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
  timeout/row/byte disclosure, and snapshot provenance;
- structural confirmation, no-confirmable warnings, stale-row dropping, and
  nullable graph-reference metadata;
- both v4 flags default-false, independently enabled, and enabled together;
- v4 fact/entity confirmation and channel caps; and
- descriptor, function-signature, and manifest-hash determinism.

Broad supported-Python suites run in CI. The known LadybugDB INT128 traversal
flake is rerun when it appears; it is not treated as evidence for unrelated
code changes.
