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

## The Cypher gate is a deny-scan for what `read_only` does not stop

Two measured facts about the pinned engine (`ladybug==0.18.2`) define the
control split:

1. `Database(..., read_only=True)` refuses every mutation form on its own —
   SET, DELETE, CREATE, MERGE — with
   `Connection exception: Cannot execute write operations in a read-only database!`.
   The gate does not need to detect mutations to be safe.
2. `read_only=True` does **not** block the file/network/extension family.
   Those are the only constructs that must die before the engine sees the text.

So the pre-engine scan refuses, by name, only: `COPY`, `LOAD`, `INSTALL`,
`UNINSTALL`, `ATTACH`, `IMPORT`, `EXPORT`, `CALL`. Detection is a token scan
that ignores text inside single quotes, double quotes, backticks, `//` line
comments and `/* */` block comments — and NOT `--`, which this engine does not
treat as a comment (verified). One statement per request still holds (a
trailing `;` followed only by whitespace or comments is one statement), and
the 32 KiB text cap still holds.

Mutations that reach the engine are mapped from that pinned connection
exception to `cypher_not_allowed`, so the caller gets a stated refusal rather
than a raw engine message. The wording is pinned in one place
(`READ_ONLY_REFUSAL` / `is_read_only_refusal`) with a test that asserts the
live engine still produces it for a known mutation — a version bump that
changes the string fails loudly instead of reclassifying writes as execution
errors. Other engine errors keep their current mapping (parse/binder →
`cypher_parse_error`, else `execution_error`).

The scan deliberately does **not** compile with `EXPLAIN` first. A second
compile was measured at 49–74% of a query's own runtime and buys nothing once
`read_only` already refuses writes. `explain_cypher` still prepends `EXPLAIN`
for the plan path; ordinary `query_cypher` executes directly.

What was deleted from the earlier gate, and why: the hop-range parser, the
relationship-bracket classifier, the recursive-mode keyword stepper, and the
graph-reference walker. That machinery was a lexer doing a parser's job. Three
review rounds found seven defects in it, four of them introduced by fixes to
earlier ones (`--` treated as a comment when the engine does not; `*` inside
property maps and inline predicates read as traversals; list literals and
subtraction-before-a-list read as relationship brackets; a comment between `-`
and `[` hiding a relationship pattern; label/property extraction matching
inside quoted prose). The deny-scan has no structural understanding beyond
tokens and comments, so those classes of defect have nowhere to live.

## DEVIATION from design §3.5: the hop cap is a resource limit, not a syntax rule

Design §3.5 calls the engine's 30-hop recursive upper bound "an executor hard
cap" and describes refusing over-bound and unbounded variable-length patterns
in the parser. That is no longer how this surface works.

The hop bound is now a **resource** limit, enforced the same way other cost is
enforced: `connection.set_query_timeout(...)` (already called in
`cypher_executor.py`) plus the existing row and byte caps. There is no syntax
analysis of `*`, `*1..`, or `*1..N`. An unbounded traversal is not refused by
name; it is bounded by the timeout and the result caps, and — when built — by
the process-isolated worker.

That makes the **process-isolated worker load-bearing rather than
defence-in-depth**. Design §3.5 put the parser first as the mandatory control
and the worker second as defence-in-depth. With hop cost no longer killed in
the gate, an expensive traversal that runs in the API process is a real
availability risk until the worker exists. The worker was already unbuilt
(below); this deviation makes that gap sharper, not softer. State it plainly:
this is not "the same cap, implemented elsewhere" — it is a different kind of
bound, and the supervisor that can outlive an engine fault is now the thing
that has to carry it.

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

The connection and the provenance that describes it are read as one act, under
the reader's refresh lock. Taking them separately left a window in which a
refresh swapped generations between the two, so rows from one generation could
be labelled with another's cut — and since provenance is the entire basis of
this grade, a result that misdescribes its own generation is worse than one
that returns nothing.

No published snapshot fails `p2_unavailable`. An empty graph and an absent
graph are different answers and must not read the same.

A Cypher answer names no SQL schema (§4.4): it did not read the `memory_v1`
views, and crediting them would tell a caller their rows came from somewhere
they never queried.

## §4.4 graph references are empty (gap)

`referenced_graph_types` and `referenced_graph_properties` on `QueryResult`
were populated by the graph-reference walker deleted with the hop/bracket
scanner. They are left empty. Reintroducing text heuristics would re-open the
quoted-prose false positives that walker had. Filling them correctly needs the
engine's AST (or an equivalent structural source), not another scan. Until
then the fields stay empty and this note is the record of the gap.

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

**The process-isolated engine worker.** The design put the parse gate first as
the mandatory control and the worker second as defense-in-depth. The worker is
not built: Cypher still executes in the API process, bounded by the engine's
own query timeout and the row/byte caps. After the hop-cap deviation above,
that worker is **load-bearing** for expensive traversals and for surviving an
engine fault mid-query — we have *observed* the pinned engine fault
mid-traversal (INT128 overflow, issue #144) rather than merely imagined it.
It is a substantial change (snapshot path plumbing, an RPC boundary, a
supervisor deadline) and is called out rather than half-built.

**`question_context` v4.** The existing context operations do not yet gain the
fact-backing and entity-candidate channels, the two default-false flags, or
P2-confirmed acceleration with PostgreSQL fallback. They work as they did; they
are not yet wired to this surface.

Both are Batch D scope by §11, so this slice does not close Batch D. Saying so
here is cheaper than a reader discovering it from the diff.


## The process-isolated worker: decided against, not deferred

§11 lists a process-isolated engine worker. It is not built, and this records
that as a decision rather than as debt.

The evidence that prompted it was the engine faulting mid-traversal (INT128
overflow, issue #144). Re-examined, that fault RAISES — it is caught and mapped
like any other engine error, and it does not wedge the process. A runaway
traversal is bounded by the engine's own query timeout and by the row and byte
caps. A supervisor, an RPC boundary, and snapshot-path plumbing would therefore
be defending against a failure mode nobody has observed, which is the
speculative hardening this project has ruled out elsewhere.

Two observations would change the answer, and either should reopen it:

- an engine fault that HANGS rather than raising, so a timeout is the only
  thing that ends it and the API process is occupied until then; or
- a fault that corrupts state shared with the API process, rather than failing
  the one query.

Until then, the timeout and the caps are the bound.
