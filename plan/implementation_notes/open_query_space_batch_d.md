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

The comment forms the scan skips are exactly the ones the pinned engine skips —
`//` and `/* */`, and NOT `--`, which this engine does not treat as a comment
at all. Skipping a form the engine does not skip would make the scan blind to
text the engine goes on to parse, and that is the one direction a gate must
never be wrong in. Every statement of that shape happens to fail the engine's
own parser today, but that is a property of this dialect rather than something
worth depending on, and it was verified against the pinned engine rather than
assumed.

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
grade `snapshot_graph`.

`built_at` is now the EXPORT TRANSACTION's own `transaction_timestamp()`,
captured once when the repeatable-read export opens and carried through
publication. It was previously the registry row's `DEFAULT now()`, written
before the export began — so the disclosed cut preceded the data it described,
and every answer was scoped to an instant the snapshot had not yet reached.
That is the guarantee the whole `snapshot_graph` grade rests on, so it is taken
from the transaction that read the data rather than reconstructed afterwards.
The reader surfaces it, having previously read it from the registry and
dropped it.

No published snapshot fails `p2_unavailable`. An empty graph and an absent
graph are different answers and must not read the same.

## Variable-length patterns must state their bound

The engine runs `*` and `*1..` quite happily; its binder only refuses an
explicit upper bound above its own 30-hop limit. A gate that reads a bound when
one is given and shrugs when none is would have a cap in name only, so a
pattern stating no finite upper bound is refused. The `*` is only read as a
traversal inside a relationship pattern's brackets — `count(*)` and
multiplication are not hops — and §3.5's recursive modes (`SHORTEST`,
`ALL SHORTEST`, `WSHORTEST(property)`, `TRAIL`, `ACYCLIC`) are stepped over so
the range after them is the one that gets read.

## `confirm=true` is narrow, and says so

It checks live membership of top-level `Entity` and `RELATES` VALUES in
PostgreSQL and drops rows whose ids fail, as units.

It does NOT yet confirm a scalar projection of `Entity.id` or
`RELATES.relation_id` — `RETURN e.id` comes back unconfirmed with all three
counts at zero. §3.5 asks for those too, and doing it correctly needs the
parsed Cypher AST to know that a particular UUID column derives from
`Entity.id`; guessing from column names or from the value's shape would drop
`Document` ids, which §3.5 says are never confirmed. Until the AST is
available, the disclosure reports zero rather than reporting a confirmation
that did not happen — but a caller who projects ids and reads
`nominated = 0` as "all clear" would be misreading it, which is why it is
written down here and in the surface documentation rather than left implicit. It does not re-run the
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

## Not built in this slice, and why

§11 lists two more things under Batch D. Neither is done, and neither is
hidden here:

**The process-isolated engine worker.** The design puts the parse gate first
as the mandatory control and the worker second as defense-in-depth, and the
gate is built. The worker is not: Cypher still executes in the API process,
bounded by the engine's own query timeout. This matters more than a
belt-and-braces argument usually would, because we have *observed* the pinned
engine fault mid-traversal (INT128 overflow, issue #144) rather than merely
imagined it — an engine that can fault is an engine worth putting behind a
supervisor that can outlive it. It is a substantial change (snapshot path
plumbing, an RPC boundary, a supervisor deadline) and is called out rather
than half-built.

**`question_context` v4.** The existing context operations do not yet gain the
fact-backing and entity-candidate channels, the two default-false flags, or
P2-confirmed acceleration with PostgreSQL fallback. They work as they did; they
are not yet wired to this surface.

Both are Batch D scope by §11, so this slice does not close Batch D. Saying so
here is cheaper than a reader discovering it from the diff.
