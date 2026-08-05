# Batch D reconciliation: close the graph slice without invented machinery

Status: non-binding implementation analysis for Batch D. The accepted contract
remains `plan/designs/open_query_space_design.md` until that document and the
decision log are amended.

## Problem

The Batch D branch implements the PostgreSQL graph helpers and most of the
Cypher executor, but its implementation note records mutually incompatible
positions and the branch does not close all of the accepted Batch D scope.
The gaps are visible in:

- `plan/implementation_notes/open_query_space_batch_d.md`, especially “§4.4
  graph references are empty”, “Not built in this slice”, and the later
  “process-isolated worker: decided against” section;
- `src/rememberstack/surfaces/query_sandbox/cypher.py`, which default-denies
  statement openings but does not reject external-action tokens appearing
  later in an otherwise readable statement;
- `src/rememberstack/surfaces/query_sandbox/cypher_executor.py`, which reports
  unavailable graph-reference metadata as two empty sets and can report a
  zero-count confirmation without warning that no value was confirmable;
- `src/rememberstack/spine/recipes.py`, where `question_context` is still v3;
  and
- `src/rememberstack/spine/query_space/manifest.py`, where the three assured
  operation descriptors are still an empty placeholder.

## One root error

The WIP tried to recover parser-level meaning with a handwritten text walker.
Review found repeated cases where the walker disagreed with the pinned engine.
Deleting that guesser was correct. Treating every contract previously fed by
the guesser as an empty value was not: an empty set means “known to reference
nothing”, while the implementation actually means “not available”.

The simplest correction is to keep text handling lexical and conservative,
use the engine as the Cypher syntax authority, and represent unavailable
metadata explicitly.

## Recommended binding changes

### 1. Gate external actions everywhere; let read-only enforce mutations

Keep the five opening forms as a default-deny statement-kind check. In the
same quote/comment-aware token pass, reject the pinned external-action,
extension, session, maintenance, attachment, and plan-control keywords
wherever they occur. This closes the current `MATCH ... CALL/LOAD/...` hole
without rebuilding a Cypher parser. Mutation forms may reach the engine and
are rejected by its pinned `read_only=True` behavior, then mapped to
`cypher_not_allowed`.

This deliberately narrows the accepted design’s claim that every mutation is
rejected before engine execution. The security property is unchanged: writes
do not succeed, while constructs that `read_only=True` does not stop are
rejected before execution.

### 2. Treat 30 hops as the pinned engine limit

`plan/analysis/p2_spike_battery.md` records the pinned engine rejecting a
recursive upper bound above 30. Do not duplicate that grammar in a text
walker. Publish 30 as engine-native and keep timeout, row, and byte bounds as
the executor’s independent resource controls.

### 3. Publish unavailable graph-reference metadata as null

Change `referenced_graph_types` and `referenced_graph_properties` from empty
tuples to nullable tuples. Cypher results use `null` until the pinned engine
exposes a structural parse result suitable for exact extraction. This neither
guesses nor falsely asserts an empty dependency set.

### 4. Define confirmation by values the engine actually typed

`confirm=true` confirms top-level structural `Entity` and `RELATES` values.
Scalar UUIDs remain snapshot-scoped because the result API does not expose
their source expression. When no top-level value is confirmable, return the
zero counts required by the contract and add a warning saying that nothing was
checked. Do not infer authority from column names or UUID shape.

### 5. Do not build a nominal “worker” without confinement

A Python child process alone supplies fault separation, not the accepted
filesystem and network confinement. The current repository has no portable
sandbox/RPC facility that supplies those controls, and the observed LadybugDB
INT128 failure raises to the caller rather than hanging or corrupting the API
process. Adding an unsandboxed subprocess and calling it the accepted worker
would overstate the security boundary and add snapshot-path plumbing without
meeting the contract.

Keep the pre-engine external-action gate load-bearing. Reopen process
isolation only when the hosting layer provides real filesystem/network
confinement, or when an observed engine hang/corruption demonstrates a fault
boundary is needed. This is an explicit YAGNI removal, not hidden debt.

### 6. Complete `question_context` v4 and discovery now

Implement v4 as one maintained compound operation over existing authorities:

- preserve hybrid claim and chunk retrieval;
- with `include_facts=true`, reuse `current_context`’s semantic nomination,
  PostgreSQL confirmation, both-stance backing, fixed evidence depth 3,
  30-fact ceiling, and 60-association budget;
- with `include_entities=true`, take exact resolution candidates first, then
  semantic entity nominations, deduplicate by survivor ID, confirm the
  combined IDs once through `memory_v1.entities_current`, and cap the channel
  at 20; and
- keep both flags false by default.

The accepted design mentions P2 acceleration for context operations but does
not specify an input that requests graph expansion or a selection algorithm
for it. `multi_hop_context` already owns explicit graph expansion and confirms
its nominated paths/edges in PostgreSQL. Do not silently add graph neighbors
to `question_context` or `current_context`; that would change answers without
a caller-visible request. A future measured descriptor can add an explicit
graph option.

Populate the three assured-operation descriptors in the checked-in manifest
from the canonical recipes in the same change, so the public v4 catalog and
`surface_manifest_hash` roll atomically.

## Verification boundary

The focused gate is: lexical adversarial cases, real read-only engine canary,
typed confirmation behavior, v4 flag independence/together, entity/fact
PostgreSQL confirmation, descriptor/hash determinism, and the existing Batch D
PostgreSQL helper fixtures. Broad interpreter matrices remain CI work.

## Post-build contract audit

The final Codex audit found four places where implementation had again used a
shape as authority rather than carrying the existing authority through:

- the P2 exporter still read legacy raw projection views, so active endpoints
  could survive after their last D48/D54 provenance disappeared;
- `confirm=true` trusted forgeable map labels without checking the engine's
  `NODE`/`REL` result types, while scalar `INTERNAL_ID` values exposed physical
  offsets;
- historical helper rows renamed live-at-read support fields without their
  `_current` suffix; and
- a reader cache key named only a caller-supplied version rather than the
  deployment and immutable registry snapshot identity.

The minimal correction is to reuse `memory_v1.entities_current`,
`documents_live`, `graph_edges_visible_history`,
`entity_document_mentions`, and `document_crossrefs_live` in the export; use
the engine's column types for confirmation and reject `INTERNAL_ID`; preserve
the `_current` names; and key/verify the cache by deployment, snapshot ID, and
a validated leaf version. No new visibility subsystem, parser, or retention
policy is needed.

The same audit found two contract-disclosure gaps. Cypher must share the SQL
surface's existing kill-switch/admission/audit objects, and the manifest must
publish the already-binding exhaustive P2 property table rather than only a
contract label. Those are composition and disclosure corrections, not new
features. Pure PostgreSQL graph helpers need no elevated bridge authority, so
their existing `SECURITY INVOKER`/`PARALLEL SAFE` implementation is the smaller
and safer binding; only projection-backed functions require the definer
bridge.
