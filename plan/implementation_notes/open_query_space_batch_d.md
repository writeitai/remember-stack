# Batch D — the graph surface

Two ways to ask the graph a question, and one rule about what an answer means.

## Bounded helpers read the same graph the views publish

`graph_neighborhood` and `graph_path` are PostgreSQL functions over the
`memory_v1` edge views. With no instants they read `graph_edges_current`, so a
traversal and a direct read of the current graph cannot disagree about what is
current — a helper that walked relations the current view adjudicates away
would contradict the very view it is a shortcut for. With `valid_at` or
`believed_at` supplied they read `graph_edges_visible_history` under both D41
clocks, each half-open, and each null endpoint treated as an open interval.

Traversal is undirected: a relation is an assertion about two entities, and
answering only from the subject side would silently answer half the question.
The rows keep their real subject and object, so direction is still visible.

A relation is walked at most once per branch, a path never revisits an entity,
and the start is not reported as its own neighbour — every relation incident to
it is already a hop-1 row, so walking back to it can only repeat what the
caller has, one hop further away than it really is. Every bound is clamped
inside the function and each walk is ordered before it is cut, so a bound means
one thing rather than a different subgraph per run.

## The Cypher gate is the mandatory control

`read_only=True` blocks writes but does NOT block `COPY`/`LOAD`/`INSTALL`-class
file, network, attachment, and extension actions. So every mutation and
external-action construct is rejected by name BEFORE the engine sees the text,
including one hidden in a `UNION` arm, behind a comment boundary, or inside a
subquery. Quoted prose and comments contribute nothing to the scan, because a
keyword inside a string is data.

The scan is deliberately blunt in one direction: a caller who names something
`create` is inconvenienced, and a caller who hides `CREATE` anywhere is
stopped. After the gate, the pinned engine's own parser decides whether what
remains is a query it implements — syntax it does not implement fails
`cypher_parse_error` rather than being rewritten into a different query.

`EXPLAIN` is on the reject list for caller text and is prepended by the surface
itself, so `explain_cypher` cannot be reached by smuggling one into a query.

## Every answer carries the instant it projects

A snapshot answer is exactly correct for its cut and says nothing about what
has happened since, so `built_at` travels with every result along with the
grade `snapshot_graph`. `built_at` is the export transaction's timestamp, not
the publish time and not a wall clock read at query time; the reader now
surfaces it, having previously read it from the registry and dropped it.

No published snapshot fails `p2_unavailable`. An empty graph and an absent
graph are different answers and must not read the same.

## `confirm=true` is narrow, and says so

It checks live membership of top-level `Entity` and `RELATES` ids in
PostgreSQL and drops rows whose ids fail, as units. It does not re-run the
plan, re-ground an aggregate, or make any other part of the result live, and
the result keeps its `snapshot_graph` grade. Asking for it without a
PostgreSQL connection is refused rather than ignored: a caller who asked for
confirmation and did not get it would read the result as confirmed.

## Engine offsets are not identifiers

Structural values keep their labels and exposed properties and lose the
engine's physical offsets, which are stable only inside one built generation.
The pinned engine spells these keys in upper case (`_ID`, `_LABEL`); a
case-sensitive strip silently published all of them, and the confirmation path
silently matched nothing, which is how the test found it.
