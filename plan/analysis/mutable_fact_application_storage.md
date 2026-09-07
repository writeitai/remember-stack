# Concrete application storage for D114

**Status:** non-binding implementation analysis, 2026-09-07.
**Baseline:** main `8fad369d` plus the reviewed D114 design. This is replacement
work, not revival of the archived framework.

## What the existing implementation already supplies

`spine/work_ledger.py` and `spine/observation_adjudication.py` supply entity work
units, source-version membership, claim completion barriers and ordered flush.
`spine/forget.py` supplies the deployment forget fence and ordinary-work drain.
`spine/clustering.py` supplies the identity-epoch lock. `spine/lifecycle.py`
updates claim testimony currency and recounts fact evidence. Adjudication tables
already hold a JSON features object suitable for a decision and before/after
values. None needs a second temporal scheduler or operation history.

Two missing identities cannot be replaced by statement equality: the complete
normalization response for a source/generation, and one application of a
particular normalized output. `FactCatalog.upsert_relation` currently creates or
merges before semantic identity. Observation exact/date gates likewise cannot be
retained. Both planes can share the existing entity work topology, while retaining
their different fact tables and semantic candidate blocks.

## Selected storage direction

Use two new internal tables: immutable complete normalization outputs, and
application records for their resolved assertions. The application is staging,
prepared inference and completion receipt in one row. Keep current assertion
support pointers in that row, separately from its original result. Aggregate
claim-to-fact evidence from those pointers. Existing legacy evidence needs one
boolean marking support not represented by an application; this prevents moving
one new assertion from accidentally deleting independently retained evidence.

A mutable `window_claim_ids` array on a fact names the evidence grounding its
current whole window. It is not a permanent seed or per-endpoint owner. It makes
forgetting a cited source discoverable without replaying a graph of operations.
If its support is removed, clearing the whole window is conservative; an ordinary
new adjudication can restore a supported window. The original claim dates never
get reduced into a second fact window.

Alternatives rejected: one new table per lifecycle phase duplicates authority;
a generic temporal journal recreates D110; exact text/triple keys collapse
independent events; inferring completed applications from evidence loses retry
results after a split or source withdrawal. Storing only the first normalized
assertion risks a retry silently replacing the rest of the response.

## Coordination and inference

Reuse deployment forget, identity and entity locks, then lock the consumed claim
and fact rows in a documented order. Remote inference runs after releasing them.
On application, reacquire those locks and compare the current full consumed
content to the prepared fingerprint, including candidate membership. Equivalent
inputs have equivalent authority; no per-endpoint history or new block revision
ledger is required. Candidate facts include completed world intervals. Claims'
`is_current_testimony` is an actual mutable row field, so a claim row lock also
coordinates with the D54 currency writer.

The protocol must inspect existing identity, lifecycle and forget writers, not
merely protect the new writer. Block locks protect fact creation/identity and
support moves; row locks protect input values and currency updates; the forget
fence prevents late inference from republishing deleted inputs. The implementation
must prove a helper that waited for locks re-reads after acquiring them.

PostgreSQL documents row-lock conflicts and transaction-scoped advisory locks in
[Explicit Locking](https://www.postgresql.org/docs/19/explicit-locking.html), and
per-command snapshots in [Transaction Isolation](https://www.postgresql.org/docs/19/transaction-iso.html)
(retrieved 2026-09-07). These are reasons for an explicit locked re-read; a
READ COMMITTED transaction beginning earlier is not a frozen application snapshot.
This does not claim PostgreSQL 19 is a stable upstream release; it is the engine's
existing test target.

## Boundedness and refusal

Prepared model inputs must be bounded and disclose candidate/evidence truncation.
A positive match or cap can target only supplied facts and cite only supplied
claims. A historical support move requires its original assertion and all affected
support locations to be supplied. An incomplete input cannot justify deleting
unseen support. SQL candidate membership can be fingerprinted using streamed
structural metadata without retaining another copy of all source text.

Keep deterministic source ordering inside finite admitted sets. A late-arriving
source joins a later set; it does not retroactively change an already completed
application. Existing version barriers continue to require every admitted output
and every member's completion, including relation outputs.

## Migration and deletion costs

D114 changes the meaning of existing fact dates. Conversion must run with serving
and ordinary writers stopped; source-time values cannot simply be called world
time. The fact window defaults to unknown until an existing adjudication or
admissible source window actually grounds it. Current consumers and fact writers
must switch together before readiness opens. Experimental D110 databases are not
silently downgraded.

Prepared payloads can include other sources. They therefore carry an explicit
input-claim inventory for targeted invalidation on forget. Applied decisions use
the ordinary transcript; that transcript must similarly expose its complete
consumed-claim inventory for scrubbing. A structural completed result can remain
without a payload and must never recreate evidence or dates on retry. Mutable
window witnesses and derived labels/vectors are cleared when contaminated.
Existing hard-forget non-resurrection and shared-fact rules still apply.

## Verification before enabling the write path

The binding companion must pin exact DDL, output shape, support-move semantics,
barrier membership, lock order, fingerprint content and deletion operations.
Antigravity and Grok review it before replacement write-path code is accepted.
The already implemented pure window value/math tests exercise approved D114
semantics independently of this not-yet-enabled application path.
