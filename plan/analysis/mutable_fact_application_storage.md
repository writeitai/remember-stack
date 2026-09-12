# Concrete application storage for D118

**Status:** non-binding implementation analysis, 2026-09-07.
**Baseline:** main `8fad369d` plus the reviewed D118 design. This is replacement
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

D118 changes the meaning of existing fact dates. Conversion must run with serving
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
The already implemented pure window value/math tests exercise approved D118
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


## Consumer recovery review (2026-09-07)

Grok's focused runtime audit found that the existing version-level label work
cannot be re-fired after it succeeds, scans only facts evidenced by that source,
and loses predecessor/object targets because drain returns only the selected
fact. Existing profile and K refresh stages in the enum have no production
handlers; inventing work on those stages would strand repairs. The correction
must retain the applied changed-fact IDs and use the real label/profile/K paths,
with a per-application work identity on the existing ledger. This is recovery
of disposable projections, not a new fact correction store.

Antigravity independently audited deletion. A legacy shared observation retained
its original source text, and the old exclusivity test treated a surviving
contradiction as support for retaining that assertion. The accepted correction
is to retain a shared identity only with independently surviving current positive
support, reconstruct its prose from a surviving original normalized assertion
(or surviving legacy positive claim text), and delete the unsupported assertion.
No model inference is needed during purge; counterevidence never becomes the
replacement statement. Recompute exclusivity when honoring older portable
manifests, clear a whole window whose witness is erased, and scrub all consumed
transcript/preparation payloads. Existing null profile hashes identify caches
requiring repair after a crash. The original manifest bytes remain immutable.

The application contract already permits source cascades. Antigravity's claim
that every cascade is forbidden is too broad; explicit source-owned deletes are
nevertheless small and make this inventory easy to audit. Both reviews are
focused findings, not final runtime approval.

## Retained-claim replay and runtime review, 2026-09-07

Grok's conversion review found that generic version backfill enumerates old work,
not retained claims, and inserts into a lane the stock worker does not consume.
The maintenance seeder therefore enumerates the claims table directly, retains
historical extractor outputs, selects a real surviving chunk occurrence, and uses
ordinary claim-normalization work on the existing steady lane while intake is
fenced. The ledger's unique generation key is its resume cursor. There is no
conversion queue or re-extraction. A claim without source coordinates blocks
conversion rather than disappearing from coverage.

A further audit found that version barriers pin one extractor and representation.
That is right during ingestion but insufficient for a retained-store conversion:
several historical extractor outputs may belong to one version. While the existing
NULL readiness fence holds, completion counts every retained claim occurrence in
the version and frozen application membership spans those same occurrences. It
does not wait for retired extractor jobs. The ordinary pinned behavior resumes
when the verifier opens the generation. Verification checks source receipts,
accepted output ordinals, version completion, evidence accounting, pending work,
and durable projection repairs before the single readiness update.

Antigravity's writer review identified two avoidable costs: one stale vector
aborted other already-paid vectors in its batch, and evidence-only changes cleared
unchanged fact embeddings. Both are removed. Changed dates reject the stale vector;
the newer application's ordinary repair work owns rebuilding it. Fact text/date
changes clear fact embeddings; evidence-only changes still repair entity profiles.

Other findings require distinguishing the contracts. Original application results
remain stable after an explicit later support move; the mutable support pointer
is checked separately on retry. Rewriting that receipt would violate replay
semantics. Hard-forget must also remove consumed claim IDs from applied receipts:
retention of an audit reference is not an exception to erasure. The scrub now takes
the existing exclusive deployment fence directly as well as relying on the durable
forget marker established by preparation. The claimed entity-profile deadlock was
not established: writers update all affected entity rows in UUID order; the profile
refresher takes all advisory locks before its single entity row lock and does not
then wait for a fact lock. Removing synchronous invalidation would instead permit
a stale cached profile to appear current until queued repair runs.

`changed_fact_ids` is retained in the structural receipt because an original target
and created IDs cannot recover an updated predecessor or the source of a support
move. This is the same receipt feeding the existing work ledger, not another
payload store. The contract's earlier closed-field sentence has been corrected to
include this already-reviewed repair inventory.

## Rebase and final integration findings, 2026-09-07

Main acquired D114–D117 and migrations through `p9_29_0050` during this work.
The mutable-fact decision is now D118 and its migration is `p9_30_0051`.
Main's renamed context operations remain intact; the new date contract advances
LoCoMo from main's Full-v26 to Full-v27. Model-binding provenance names the actual
fact adjudicator rather than the retired observation/supersession ladders.

Grok round 3 closed the earlier premature-cutover findings: current-generation
failed work blocks verification, source-less claims are reported without rolling
back other seeded claims, and K reads/publication remain fenced during conversion.
It also caught a test-composition gap: the conversion rig omitted the fact catalog
from the downstream handler. That dependency is now identical to production, and
acceptance must show document-label follow-up without testimony reconciliation.
A retained claim can normalize to no assertions; document repair therefore also
refreshes profiles from its retained fact links, independent of new applications.

The remaining K/forget finding was valid but its suggested fix was insufficient.
Skipping K compilation while conversion is closed avoids a recovery deadlock, but
the actual Git purger retains current bytes when rewriting history. Simply skipping
would restore forgotten text in a new commit. The existing K driver instead removes
affected machine-owned bodies through its ordinary checkout/publish port and commit
lease. PostgreSQL erasure has already cleared their content attestations and left
them stale. The normal purger can then erase history without restoring those bodies;
normal compilation recreates them after conversion. Authored/curation preflight
remains in force. No new storage, port, scheduler or alternate fact reader is needed.

Integration also found two existing behaviors missing from the replacement writer:
new relations must increment predicate usage once per identity, and cross-identity
window edits must remain visible to the existing unmerge review scan. The writer
now records its related fact in the existing transcript column; unmerge includes
ordinary `update` rows as well as historical `supersede` rows. It flags review,
without automatically undoing dates. Tests cover ordinary retry, evidence attachment,
recursive merged members and same-identity edits. Unreachable old mutation bodies
are removed; rejected compatibility entrypoints and the historical read-only pair
diagnostic remain. That diagnostic is not evidence of replacement-writer quality.

### Final review follow-through: Git erasure and end-to-end proofs

Grok's final review of `73a3df32` found no runtime blocker in its inspected scope,
but correctly identified stale shipped documentation and two missing composition
proofs. The project-status page and ingestion withdrawal description now describe
the replacement runtime. A retained-store conversion test also covers an empty
normalizer response: the ordinary document-label job must refresh both entity
profiles despite creating no fact applications.

The real-Git composition proof found a recovery bug hidden by the earlier fake
remote. After deleting a compiled body and rewriting its history, the purger
called `git add` with the now-absent path and failed. It now stages only surviving
current bytes; history rewriting already removed the deleted paths from the
index. The test runs deletion, publication, history purge and verification twice,
checks that the source marker has no reachable history, and preserves unrelated
current pages. The same proof includes a tombstoned compiled artifact, which is
included explicitly in the erasure path inventory. Ordinary checkout classification
continues to exclude tombstoned artifacts. No new erasure store or scheduler is
introduced.

## No existing-store conversion (2026-09-11)

The operator decided that stores populated before D118 are recreated, not
converted: the product is unreleased and no populated deployment needs the
in-place path. The design and contract now state this as a scope boundary. The
implemented conversion was withdrawn on the same branch. Concretely this removed
the deployment readiness column and the serving/intake fence it drove in the
query engine, the query-role connection factory, forget admission and every K
compile and commit step; the maintenance seeder and verifier and their CLI; the
conversion branches in the E3 follow-up handler and the K forget driver; the
snapshot mode that admitted system-closed facts as candidates; the barrier SQL
that spanned retired extractor generations; and the legacy evidence stance with
its whole-claim support move in the decision schema. The migration now refuses a
database that holds claims instead of clearing its windows.

Three smaller changes landed with it, each reasoned here so a later reader can
tell a design rule from a tuning choice:

- **Empty candidate set decides without the model.** When the entity has no
  candidate facts there is nothing to compare; the only valid answer is a new
  fact. Preparation records that answer itself under the same attempt and
  fingerprint checks, and the transcript uses `novelty_gate`. This restores the
  cheapest shortcut main had without restoring any text-equality merge.
- **Exact matches are always nominated.** Full-text ranking alone could push
  the same-triple relation or identical statement out of the twenty-fact window
  on a busy entity, and those are the likeliest identity candidates. They are
  now the first tier; ranking fills the rest. Nomination only; no identity rule.
- **Direct lookups return possible matches flagged instead of dropping them.**
  Facts context already returned undated facts marked `possible`; the relation
  and observation lookups dropped them and reported only a count. The same
  question gave different answers through two surfaces. Both now return the
  flagged list. The strict published SQL view keeps excluding them.

The real-Git purge fix found during the conversion work is not conversion
specific: a compiled page whose file is already absent from the checkout (a
retired page removed by an ordinary K cycle) made the purger stage a missing
path. The fix and its real-Git test stay, exercised through that ordinary path.

Points that are sound on paper but need observation on real corpora are kept in
[the watch list](mutable_fact_windows_watch_list.md).
