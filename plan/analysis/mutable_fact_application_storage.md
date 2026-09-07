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


## Independent application-contract review, 2026-09-07

Antigravity approved with fixes; Grok requested changes before runtime wiring.
Both reviewed commit 8bbb48e3 against 8fad369d. Grok correctly identified that
D90's replacement must explicitly serialize the least pending application across
unlocked inference, include redirected subjects, and name the removal of source
withdrawal's world-time cap. The contract now pins these rules, frozen array
ordinals, plane-specific generations, structural receipt fields, indexed transcript
claim inventory, and a fenced nullable-expand/clear/constrain migration order.
Antigravity caught nullable relation staging statements and ordered currency locks.
Its JSON sorting suggestion is insufficient by itself: explicit ordered row locks
are required. These repairs use the existing work units and transcript stores.

A boolean legacy-support marker also loses the baseline stance when new assertion
support temporarily overrides a legacy contradiction. Retaining nullable
`legacy_stance` instead is one column with enough information to restore the link;
NULL means application-only evidence. This replaces the boolean in the contract.

For canonical fact values, reject unaligned bounded endpoints instead of silently
normalizing them a second time. Raw-to-canonical construction is a separate path.
Query instants normalize through the existing UTC rule, matching claim helpers.

The closed answer carries the existing evidence `stance` on its incoming target.
Otherwise contrary testimony about the same event could only be attached as
support or turned into another fact. This is the existing claim/fact evidence
relation, not a stored date-dispute status. Every minted handle also requires an
explicit evidence assignment; merely updating its window cannot create a fact.

Conversion readiness needs one persistent deployment field so a process restart
cannot reopen serving after partial replay. `fact_window_generation` is NULL while
converting and the current generation string when verified. Empty stores can start
ready. This is one column on the existing deployment, not a conversion step ledger.
The existing work receipts and barriers supply bounded restart progress.


## Intermediate writer review resolution

Antigravity and Grok identified sticky invalid answers, audit labels applied too
broadly, receipt checks and canonical witness ordering. Rejected answers now roll
back effects using the existing SQL transaction/savepoint, then clear the obsolete
attempt before the ledger retries; no extra queue or state table is introduced.
Staging retirement covers only versions with materialized entity units. A later
D56 membership is also reconstructed from the frozen receipt at its version
barrier, so an application completed by another helper cannot erase that membership.
Post-lock nomination compares participant sets; substantive input differences
still invalidate the complete fingerprint. Pending invalidation keeps claim inventory.

Grok also questioned attaching converted testimony to system-closed facts. That
is not itself revival: `invalidated_at` remains unchanged and believed retrieval
excludes the row. Current testimony and current belief are different axes, already
permitted on main. Historical conversion needs to preserve evidence on those IDs.
The conversion acceptance test must prove system closure is preserved; ordinary
new work continues to nominate only currently believed facts. Do not equate an
evidence-count change with reopening belief or add a date/type identity rule.
