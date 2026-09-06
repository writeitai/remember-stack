# Temporal relation staging: ordering, identity, and conversion

**Status:** non-binding independent analysis for issue #365; no implementation
or binding amendment is established by this document.  
**Inspected:** 2026-09-06.  
**Question:** how can parallel claim normalization preserve a reproducible
fact creator and avoid collapsing repeated events before adjudication?

## 1. What exists and what must change

The relevant current contracts are:

- `plan/designs/e3_claim_level_normalize_fanout_design.md` §§5.1–5.5
  (D88): a closed extraction set creates claim jobs atomically; the last
  successful claim opens a representation barrier. Relations currently write
  during those jobs. Observation candidates wait until after the barrier.
- `plan/designs/e3_entity_obs_flush_fanout_design.md` §§5.1–5.8 (D90):
  version-scoped entity units provide durable barrier membership, while the
  apply stream merges available staging across versions of one entity.
- `plan/designs/temporal_clocks_design.md` §§4.1–4.2 and §12.1 (D107):
  a fact's creator seeds its verdict window once; relation claims must remain
  unattached until the identity verdict. Section 9 preserves existing fact IDs
  during conversion.

Actual code confirms the race. `FactCatalog.upsert_relation` in
`src/rememberstack/spine/fact_catalog.py` takes a triple lock, finds an open
relation, inserts if absent, and immediately attaches evidence in its own
transaction. `NormalizeRelationsHandler._normalize_claim` in
`src/rememberstack/workers/e3.py` calls it for every proposed relation.
`SupersessionAdjudicator.adjudicate_new_relation` in
`src/rememberstack/spine/supersession.py` only sees the already-created fact;
its identical-redirected-object shortcut records a no-op. Two tournament wins
can therefore collapse before the temporal identity question is asked.

The supersession lock is already broader than the upsert lock: it covers
`(deployment, subject, predicate)`, because different objects can compete for
one changing property. A triple-only drain would leave those competitors on
different streams. Use the subject/predicate block for relation apply; retain
parallelism between unrelated blocks.

`WorkLedger.complete_claim_normalize` already discovers D56 reused-claim
occurrences in other versions, acquires their representation locks in sorted
order, and verifies each extraction barrier before opening downstream work.
This is worth preserving. It is not enough merely to add a staging write
beside the existing relation upsert.

## 2. Important contradictions and missing guarantees

### 2.1 One claim can contain several assertions

`NormalizationResponse.relations` is a tuple of zero or more
`RelationCandidate` values (`src/rememberstack/model/relations.py`). D107's
idempotency key `(triggering_claim_id, adjudicator generation)` would suppress
the second legitimate relation from the same claim. Identity must be per
normalized assertion, with the triggering claim retained as provenance.

The current claim handler also deliberately calls the normalizer again on
retry. Temperature zero is not a durable output receipt. If a retry produces
a different list, independent `ON CONFLICT DO NOTHING` writes can accumulate
outputs from both attempts. Freeze the complete accepted normalized output
set before publishing it, including an explicit empty/soft-drop receipt.
Receipt reuse must skip generating a new answer; generation changes create a
new receipt namespace. The same receipt should cover relation and observation
outputs so a claim cannot complete with a mixture of normalization attempts.

### 2.2 Available staging is not all future testimony

D90's entity-global query only sees staging whose unit has materialized.
It cannot see an earlier-dated claim from a version whose extraction or
normalization has not finished. Consequently, sorting available staging does
**not** prove invariant seed selection across every possible completion
schedule. Example:

1. Version B finishes first and creates a fact from a source dated 2024.
2. Version A, whose source is dated 2020, finishes tomorrow and supports the
   same fact.
3. A seed that is immutable after insertion cannot now become A. Reversing
   the version completion order produces a different creator.

The issue text incorrectly treats this as solved for observations. D90's
late-arrival re-split repairs a particular state-history problem; it does not
retroactively select the same first creator for all unseen future sources.

Three real alternatives exist:

| Alternative | Consequence |
| --- | --- |
| Wait for a globally closed corpus | Gives one global sorted input set, but violates continuous ingestion and couples unrelated documents to a stalled source. |
| Recompute identities and seeds on every earlier arrival | Could target stronger convergence, but contradicts once-only seeding, stable fact IDs, and recorded identity verdicts; requires a separate full identity-reconciliation design. |
| Persist admitted apply batches and their order | Preserves continuous ingestion, stable creators and exact replay; sorted order is guaranteed over each closed admitted batch, with explicit late-arrival semantics. |

Recommend the third, with **an explicit amendment of the overbroad
determinism claim**, not a test that silently avoids delayed versions. A
fresh ingest with the same closed eligible input set has deterministic
ordering independent of worker races. A replay of a store uses its recorded
admission/apply history and verdicts. A completely new ingest with a different
history of source availability need not have the same immutable seed IDs.
If identical seeds across those different histories are a required product
property, this recommendation is insufficient and the second alternative
must be designed before T.1. A decision must state which guarantee it adopts.

### 2.3 Existing contracts need more than an insertion change

- D88 §1.8's claim that relation attachment is commutative no longer covers
  temporal identity and seed choice. Replace it with staged normalization
  and ordered verdict application.
- D107 §4.2 says matching needs no exclusion, then requires a partial state
  exclusion. State clearly that locks serialize the decision and the
  constraint is a final defense; neither chooses processing order.
- D107 §4.2's undated-relation rule says relations always attach to a state,
  while §3 permits shape-classified undated occurrences. Resolve which rule
  wins; otherwise undated event normalization cannot obey the new kind.
- D107 §9 still uses `undated` as a temporal kind even though the defined
  enum is `state | occurrence | unknown`.
- Existing D90 staging selection joins processing rows without pinning the
  flush component version and admits missing rows. The new protocol must
  require the exact generation and durable materialization state. Copying
  that SQL verbatim would reproduce ambiguity rather than settle it.

## 3. Recommended complete write protocol

### 3.1 Normalize and publish candidates

Run the model and deterministic gates, resolve the proposed entities, then
publish one immutable normalization receipt and all its accepted assertions
in one transaction. A claim/generation lock makes competing attempts choose
one complete output set. An already-published receipt is returned unchanged.
Entity resolution may have separate existing side effects, but no fact or
evidence is published before the receipt. Duplicate identical resolved
assertions within the receipt collapse; different triples remain separate.

Store a stable assertion ID for each distinct output. Do not use model list
position as semantic identity: its order is not part of the proposition.
Use canonical tuple equality to establish identity and a generated ID as the
durable handle; retain the tuple in the row so a hash is never the sole
authority. Explicitly retain the normalizer's shape judgment for undated
claims. Claim dates are immutable source data: load the canonical D41 bounds
from the claim when applying, and record the actual consumed bounds in the
verdict. Duplicating mutable cached date columns in staging adds no authority.

The claim job returns success only after the complete receipt is durable.
The existing claim-barrier transaction then creates membership for every
assertion in each closed version that lists the claim. This includes newly
attached D56 occurrences discovered after the original claim job succeeded:
they reuse the stored receipt, without rerunning the model or reopening a
finished version's expected membership. No-row and zero-output are different.

### 3.2 Fan-out and stage topology

Use version-scoped **relation block units**, keyed by
`(deployment, version, normalizer generation, subject, predicate)`, with
`unit_id` as the work target. A bare subject ID would collide across versions
under the existing processing-state uniqueness contract.

Keep observation flush first. Its completed version barrier materializes the
complete relation-unit set and its processing rows in one transaction; the
new-generation `adjudicate_supersession` handler consumes these units instead
of an already-upserted relation list. This reuses an existing worker stage and
avoids a second relation identity pass. Relation units run in parallel across
blocks. The version relation barrier opens fact labeling/embedding and their
existing downstream branches only after every expected relation unit
succeeds. Pure observation/empty versions take an explicit durable empty
relation barrier path. Claim embedding can remain an independent sibling only
where it consumes claims alone; readiness must still wait for both fact
planes before claiming full fact availability.

This adds a dependency between relation apply and observation flush but does
not create a global ingestion barrier. The relation facts are necessarily
unavailable until their identity verdict. Moving profile refresh out of
normalization is required: the current `profile:normalize:{claim_id}` hook
would otherwise publish a profile before its relation evidence exists.
Refresh follows committed fact/evidence writes via the #367 repair contract.

### 3.3 Drain a closed eligible batch

Under the shared deployment identity epoch and exclusive subject/predicate
apply lock, canonicalize redirected subjects/objects and revalidate the block.
If redirects change its key, release and retry using the canonical block;
never hold two ad-hoc block locks in reversed order.

Select every unapplied assertion whose version membership is materialized at
the exact generation and whose unit is eligible (not missing, failed, or
dead-letter). Freeze that finite input set as an admitted batch before model
work. Sort distinct assertions by:

```text
asserted_at NULLS LAST, claim_id, predicate COLLATE "C",
object_entity_id, assertion_id
```

The subject/predicate block is fixed; predicate is included for a reusable
ordering representation. `assertion_id` is a final tie-breaker, not a source
clock. Record batch membership and ordinal; an assertion appearing in two
D56 versions is applied once and retires both membership links. Newly
materialized units join the next batch, even if their timestamp is earlier.
The batch record closes the replay ambiguity described in §2.2.

Hold a session lock on a dedicated connection across the batch, with short
transactions per assertion. Never return that connection to a pool while the
session lock is held. Each assertion reads the current candidate block and
uses a stored application receipt if already decided. Otherwise the ladder
decides identity and temporal effects while the block remains exclusively
owned. Reading, unlocking for a model call, and applying without version
revalidation is forbidden. A prepare/revalidate implementation is a valid
alternative only if it detects all changes to the consumed candidate block.

PostgreSQL session advisory locks survive transaction rollback; transaction
advisory locks do not. The dedicated-session lifecycle therefore needs an
explicit `finally` unlock/discard path. See PostgreSQL §13.3.5, retrieved
2026-09-06: <https://www.postgresql.org/docs/current/explicit-locking.html>.

### 3.4 Atomic apply and replay

For each assertion, one transaction performs all of:

1. Verify receipt identity, candidate revisions, claim existence and the
   deployment's forget/write fence; a deleted source cannot be resurrected.
2. For `new`, insert the relation, seed claim and temporal kind/windows,
   populated `add` adjudication, and supporting evidence. For `evidence`,
   attach support to the recorded target only. Any cap, contradiction, or
   state-history re-split produced by the verdict belongs in this transaction.
3. Recount D54 independent current-support lineages and derive occurrence
   metadata from surviving evidence without silently changing verdict bounds.
4. Write one application receipt for the assertion/adjudicator generation,
   identifying all resulting fact/adjudication IDs and the batch position.
5. Mark the assertion's version membership links applied and enqueue durable
   profile/projection repair intent.

The uniqueness constraint and all writes share the transaction; a uniqueness
conflict is resolved by reading and replaying the existing complete receipt,
not by treating a half-written fact as success. PostgreSQL's conflict clause
only arbitrates its specified uniqueness keys; it cannot make unrelated
multi-statement writes atomic. See INSERT documentation, retrieved 2026-09-06:
<https://www.postgresql.org/docs/current/sql-insert.html>.

After releasing block locks, complete only the worker's currently leased unit
and evaluate its version barrier under the established representation lock.
Peers whose inputs were already drained self-complete on lease; do not force
another worker's running/pending row to succeeded. Membership rows remain to
prove expected counts and coordinate forget. A crash after apply but before
ledger completion replays receipts, not model calls.

Use a documented global lock order shared with identity changes, corrections,
and forget. At minimum: deployment write/forget fence, identity epoch, sorted
block keys, sorted fact rows. Barrier locks are acquired after apply locks are
released. The current relation code takes a block before identity; either
retain that globally after auditing all exclusive-identity callers, or update
all callers together. A newly documented order applied to only one path is
not a deadlock fix. Read Committed gives each statement its own snapshot, so
candidate revalidation must occur inside the guarded transaction, not in an
earlier unguarded read. PostgreSQL §13.2, retrieved 2026-09-06:
<https://www.postgresql.org/docs/current/transaction-iso.html>.

## 4. Concrete relational contract proposed for the amendment

These are logical DDL requirements, not an executable migration. Use existing
UUID, generation-string, and deployment-scoping conventions. Every child
reference must enforce deployment agreement, by composite foreign key where
the referenced table has the corresponding unique key and by a verified
catalog write otherwise. Public query roles receive no grants on these tables.

| Table | Columns and constraints |
| --- | --- |
| `normalize_claim_receipts` | `(deployment_id, claim_id, normalizer_version)` primary key; `receipt_id uuid UNIQUE`; `output_count integer CHECK >= 0`; explicit outcome `accepted | empty | soft_drop`; complete input/output digest and timestamps. Both planes' complete normalized output is bound by this receipt. |
| `normalize_relation_assertions` | `assertion_id uuid` primary key; deployment/receipt/claim/doc IDs; normalizer generation; subject/predicate/object; undated shape judgment; created_at. Unique `(deployment_id, claim_id, normalizer_version, subject_entity_id, predicate, object_entity_id)`. Preserve assertion IDs across redirect resolution; redirected tuple is an apply input, not a rewrite of historical assertion identity. |
| `relation_flush_version_state` | Primary key `(deployment_id, version_id, normalizer_version)`; representation/doc/chunker/extractor/adjudicator generations, content hash, lane; state `materialized | empty_complete | barrier_complete`; expected unit count and timestamps. Never derive an empty set from absent membership. |
| `relation_flush_block_units` | `unit_id uuid` primary key; version-state reference; subject/predicate block; unique `(deployment_id, version_id, normalizer_version, subject_entity_id, predicate)`. Processing identity targets this unit ID at the exact relation-flush generation. |
| `relation_flush_inputs` | Primary key `(unit_id, assertion_id)`; deployment; nullable `applied_at`; optional application-receipt reference. Durable fixed expected membership, populated with the unit set. Same assertion may belong to several version units. |
| `relation_apply_batches` / `relation_apply_batch_inputs` | Batch ID, deployment/block, monotonically assigned block-local batch ordinal, exact adjudicator generation; input `(batch_id, ordinal, assertion_id)` with unique assertion/generation admission. Records closed sorted frontier and resumable progress. Persisted serial comes from the guarded block stream; timestamps are audit only. |
| `relation_application_receipts` | Primary key `(deployment_id, assertion_id, adjudicator_version)`; batch/ordinal, target fact IDs and adjudication IDs, consumed input digest, completion instant. Existing adjudication rows retain triggering claim plus new assertion reference; receipt is the identity decision's unique replay handle. |

Index units by version and by deployment/block; index unapplied inputs by
unit and assertion; index assertions by deployment/claim/generation; index
batches by deployment/block/ordinal; index receipts by assertion/generation.
Dates used for ordering can be joined from claims; require an actual plan on
the resulting block query at large input cardinalities before deciding to
materialize an additional ordering column.

The input journal is durable because new document versions can reuse claims
after an earlier unit has drained. It is not a staging table that can simply
be deleted wholesale on successful apply. Hard forget must delete or scrub
these source-derived rows and their receipts, including any copied verdict
features. Projection replay consumes authoritative surviving facts/verdicts;
it must never interpret forgotten journal gaps as permission to regenerate
deleted assertions.

## 5. Scale, failure, and acceptance

Storage is O(normalized assertions + assertion/version occurrences + block
units), not O(assertion pairs). A claim producing four distinct relations
needs four assertion/application records, not four additional leased jobs.
Pairwise ladder transcripts can still dominate highly ambiguous blocks; that
cost exists because candidate comparisons decide identity, not because of
the staging queue. Measure block width, normalization receipt size, admission
latency, lock hold time, oldest pending unit, and repair lag.

Do not load an unbounded deployment into memory. Freeze eligible batch
membership set-wise, then stream its ordinal index with bounded pages while
retaining exclusive block ownership. A hot block is intrinsically serial for
identity correctness. Other blocks continue; model-provider failures preserve
the durable batch frontier and retry through the normal work ledger. A
dead-letter unit blocks its own version's readiness. Define recovery of a
partially applied multi-unit batch explicitly: another lease may finish already
admitted inputs only while their owner units remain eligible; dead-lettered
inputs must not be silently skipped to claim a fully applied batch.

Required database-backed acceptance cases:

1. Two claim workers in one closed version finish in both orders: identical
   seed, verdict windows and semantic facts, one complete output receipt each.
2. Co-present overlapping version units drain in one merged order. A later
   materializing older version tests the **explicitly chosen** §2.2 contract,
   and replay reproduces recorded seeds without calling the model.
3. One claim yields two triples: both apply; a duplicate identical triple
   yields one assertion. Retrying with a different model output does not add
   or remove outputs from the already-published receipt.
4. Same triple, two distinct occurrences: neither attaches before identity;
   `new` produces two facts even with overlapping date precision. A disjoint
   date dispute can reach `contradict` rather than being filtered out.
5. Shared D56 claim is attached to another version after first application:
   the new version receives complete membership and reaches readiness without
   a second evidence link, model call, or fact.
6. Kill before/after receipt publish, fan-out commit, assertion apply,
   application receipt commit, and unit completion. Recovery never duplicates
   facts and never treats missing membership as empty success.
7. Two last claim jobs and two last block units use separate database
   connections: exactly one downstream fan-out/barrier completion occurs.
8. DLQ, missing processing row, wrong component generation and cross-deployment
   coordinates fail closed. Empty/soft-drop receipts are successful explicit
   empty sets, not missing work.
9. Relation twin of D90's A(t1), A(t3), late B(t2): historical states follow
   canonical world-time, including a version with reversed said-on order.
10. Merge/unmerge, autonomous correction, evidence withdrawal, and hard forget
    race with relation application: no stale candidate verdict, resurrected
    source, deadlock caused by inconsistent lock order, or lost cache repair.
11. Million-document-shaped fixture: inspect fan-out and block-drain EXPLAIN
    plans, memory boundedness and hub lock duration, including reused claims
    with many version memberships.

## 6. T.1 conversion and generation boundary

The new protocol cannot run concurrently with legacy in-claim relation
upserts. Stop intake, drain all old-generation writers and durable repair
work, migrate helper tables and fact fields, then convert facts under a
deployment-level serving gate. Verify pending, running, retrying and DLQ work;
"no pending jobs" is insufficient. Preserve D106's old observation flush
handler until its units drain, rather than routing old rows into the new
temporal algorithm.

Conversion must checkpoint durable batches of fact IDs and record a migration
verdict per fact without changing fact IDs. Observations recover seed claims
from recorded add verdicts where available; legacy relation seeds remain
unknown because guessing the minimum attached claim rewrites provenance.
Build the state exclusion against all converted windows before publishing the
generation; fail closed on conflicts rather than letting a batch order choose
a winner. Retain a pre-conversion backup and require restore for rollback to
an image that cannot interpret the new semantics.

Atomically mark the deployment's temporal generation ready only after fact
conversion, constraints, all required profile/knowledge invalidation, expiry
scheduling and hard-forget inventory validation have succeeded. New consumers
must refuse mixed-generation facts while this gate is closed. Roll normalizer,
relation and observation adjudicators, affected label/embed generations and
the LoCoMo protocol according to their changed observable behavior. Persist
resolved generation strings in durable units instead of interpreting them
using the process's latest constants during retry.

This analysis does not establish the autonomous-correction or cache-expiry
contracts. It identifies the transaction and ordering hooks those amendments
must bind before T.1 implementation.

## 7. Executable PostgreSQL schema proposal

This appendix is still **non-binding analysis**. The SQL is a concrete schema
proposal for review, not permission to run a production conversion. It uses
the current `public.claims`, `document_versions`, `document_representations`,
`entities`, `predicates`, `relations`, and `relation_adjudications` names.
Their relevant DDL comes from migrations `p0_02_0002` through `p0_02_0004`;
later migrations do not rename their identity keys. Existing scoped unique
keys permit the composite foreign keys below. `processing_lane` and
`claim_valid_precision` are existing enum types, not new text conventions.

### 7.1 Precise ordering promise

**This DDL does not make admission order independent of source availability.**
The ordered input set must already be closed before its batch row and every
input row are committed. Batch membership is not chosen by a fixed row limit,
worker lease order, or arbitrary pagination. It contains the entire eligible
unapplied frontier of that block at one guarded admission instant. Pagination
only streams this already-frozen set. Within that closed set, assert order by
said-on, claim ID and assertion tuple; preserve it as `ordinal`.

For fixed closed input sets and fixed normalization/identity results, worker
completion order cannot change application order. With arbitrary later source
arrivals, only replay of the recorded batch membership and verdicts guarantees
the same creator and fact IDs. Neither the `batch_no` column nor its uniqueness
constraint turns the latter guarantee into global arrival-order convergence.
An accepted D110 must name that boundary explicitly or select the larger
identity-reconciliation alternative in §2.2.

### 7.2 Receipt and assertion storage

The JSON output is the complete **resolved, accepted** normalization answer,
including observation candidates. `normalization_output` contains arrays named
`relations` and `observations`; row counts must match those arrays. The worker
validates each array element with the typed normalization schema before
publishing the receipt. The SQL checks envelope/count coherence, while the
typed writer binds each relation row to its exact output tuple. A future
implementation must not publish the receipt in a transaction separate from
its child assertions. Existing logical claim-to-document provenance still
requires the writer to verify that `claims.doc_id = receipts.doc_id`.

```sql
CREATE TABLE public.normalize_claim_receipts (
  receipt_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES public.deployments,
  claim_id uuid NOT NULL,
  doc_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  outcome text NOT NULL CHECK (outcome IN ('accepted', 'empty', 'soft_drop')),
  input_digest text NOT NULL,
  output_digest text NOT NULL,
  normalization_output jsonb NOT NULL,
  relation_count integer NOT NULL CHECK (relation_count >= 0),
  observation_count integer NOT NULL CHECK (observation_count >= 0),
  published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (deployment_id, claim_id, normalizer_version),
  UNIQUE (deployment_id, receipt_id, normalizer_version),
  FOREIGN KEY (deployment_id, claim_id)
    REFERENCES public.claims (deployment_id, claim_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, doc_id)
    REFERENCES public.documents (deployment_id, doc_id) ON DELETE CASCADE,
  CHECK (jsonb_typeof(normalization_output) = 'object'),
  CHECK (normalization_output ?& ARRAY['relations', 'observations']),
  CHECK (jsonb_typeof(normalization_output -> 'relations') = 'array'),
  CHECK (jsonb_typeof(normalization_output -> 'observations') = 'array'),
  CHECK (relation_count = jsonb_array_length(normalization_output -> 'relations')),
  CHECK (observation_count = jsonb_array_length(normalization_output -> 'observations')),
  CHECK ((outcome = 'accepted') = (relation_count + observation_count > 0))
);

CREATE TABLE public.normalize_relation_assertions (
  assertion_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES public.deployments,
  receipt_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  subject_entity_id uuid NOT NULL,
  predicate text NOT NULL,
  object_entity_id uuid NOT NULL,
  shape_kind text NOT NULL CHECK (shape_kind IN ('state', 'occurrence', 'unknown')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (deployment_id, assertion_id, normalizer_version),
  UNIQUE (deployment_id, assertion_id),
  UNIQUE (receipt_id, subject_entity_id, predicate, object_entity_id),
  FOREIGN KEY (deployment_id, receipt_id, normalizer_version)
    REFERENCES public.normalize_claim_receipts
      (deployment_id, receipt_id, normalizer_version) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, subject_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, object_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, predicate)
    REFERENCES public.predicates (deployment_id, predicate) ON UPDATE CASCADE
);
CREATE INDEX ix_rel_assertion_block ON public.normalize_relation_assertions
  (deployment_id, subject_entity_id, predicate, assertion_id);
CREATE INDEX ix_normalize_receipt_doc ON public.normalize_claim_receipts
  (deployment_id, doc_id);
```

Claim/doc IDs are not copied into assertion rows in this concrete refinement:
the receipt already owns them, so the apply query joins receipt to claim.
That removes redundant fields whose agreement a foreign key would otherwise
need to enforce. A receipt retains the originally resolved IDs; merge redirects
are revalidated at apply, not rewritten into an old normalization answer.

### 7.3 Version membership and exact-generation work identity

```sql
CREATE TABLE public.relation_flush_version_state (
  deployment_id uuid NOT NULL REFERENCES public.deployments,
  version_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  representation_id uuid NOT NULL,
  doc_id uuid NOT NULL,
  chunker_version text NOT NULL,
  extractor_version text NOT NULL,
  adjudicator_version text NOT NULL,
  content_hash text NOT NULL,
  lane public.processing_lane NOT NULL,
  fanout_status text NOT NULL
    CHECK (fanout_status IN ('materialized', 'empty_complete', 'barrier_complete')),
  expected_units integer NOT NULL CHECK (expected_units >= 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz,
  PRIMARY KEY (deployment_id, version_id, normalizer_version),
  UNIQUE (deployment_id, version_id, normalizer_version, adjudicator_version),
  FOREIGN KEY (deployment_id, doc_id, version_id)
    REFERENCES public.document_versions (deployment_id, doc_id, version_id)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, version_id, representation_id)
    REFERENCES public.document_representations
      (deployment_id, version_id, representation_id) ON DELETE CASCADE,
  CHECK ((fanout_status = 'materialized') = (completed_at IS NULL)),
  CHECK (fanout_status <> 'empty_complete' OR expected_units = 0)
);

CREATE TABLE public.relation_flush_block_units (
  unit_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL,
  version_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  adjudicator_version text NOT NULL,
  subject_entity_id uuid NOT NULL,
  predicate text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (deployment_id, version_id, normalizer_version, subject_entity_id, predicate),
  UNIQUE (deployment_id, unit_id, normalizer_version, adjudicator_version),
  FOREIGN KEY (deployment_id, version_id, normalizer_version, adjudicator_version)
    REFERENCES public.relation_flush_version_state
      (deployment_id, version_id, normalizer_version, adjudicator_version)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, subject_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, predicate)
    REFERENCES public.predicates (deployment_id, predicate) ON UPDATE CASCADE
);
CREATE INDEX ix_rel_flush_units_block ON public.relation_flush_block_units
  (deployment_id, subject_entity_id, predicate, adjudicator_version);

CREATE TABLE public.relation_flush_inputs (
  deployment_id uuid NOT NULL,
  unit_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  normalizer_version text NOT NULL,
  adjudicator_version text NOT NULL,
  applied_at timestamptz,
  PRIMARY KEY (unit_id, assertion_id),
  FOREIGN KEY (deployment_id, unit_id, normalizer_version, adjudicator_version)
    REFERENCES public.relation_flush_block_units
      (deployment_id, unit_id, normalizer_version, adjudicator_version)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, assertion_id, normalizer_version)
    REFERENCES public.normalize_relation_assertions
      (deployment_id, assertion_id, normalizer_version) ON DELETE CASCADE
);
CREATE INDEX ix_rel_flush_unapplied ON public.relation_flush_inputs
  (deployment_id, unit_id, assertion_id) WHERE applied_at IS NULL;
CREATE INDEX ix_rel_flush_input_assertion ON public.relation_flush_inputs
  (deployment_id, assertion_id, adjudicator_version);
```

Processing rows retain existing `processing_state` schema: `target_kind =
'entity'`, `target_id = unit_id`, `stage = 'adjudicate_supersession'`, and
`component_version = adjudicator_version`. `entity` denotes the existing
unit-target convention, not a foreign key to the entity registry. Membership
and processing rows are committed together. Do not FK units directly to
processing rows: the barrier must be able to detect a missing processing row
as corrupt/incomplete state. `expected_units` must equal the committed unit
count, verified by the barrier; SQL checks alone cannot compare child counts.

### 7.4 Closed admission and application receipts

```sql
CREATE TABLE public.relation_apply_batches (
  batch_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES public.deployments,
  subject_entity_id uuid NOT NULL,
  predicate text NOT NULL,
  adjudicator_version text NOT NULL,
  batch_no bigint NOT NULL CHECK (batch_no > 0),
  expected_inputs bigint NOT NULL CHECK (expected_inputs > 0),
  admitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz,
  UNIQUE (deployment_id, subject_entity_id, predicate, adjudicator_version, batch_no),
  UNIQUE (deployment_id, batch_id, adjudicator_version),
  FOREIGN KEY (deployment_id, subject_entity_id)
    REFERENCES public.entities (deployment_id, entity_id),
  FOREIGN KEY (deployment_id, predicate)
    REFERENCES public.predicates (deployment_id, predicate) ON UPDATE CASCADE
);

CREATE TABLE public.relation_apply_batch_inputs (
  deployment_id uuid NOT NULL,
  batch_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  ordinal bigint NOT NULL CHECK (ordinal > 0),
  assertion_id uuid NOT NULL,
  PRIMARY KEY (batch_id, ordinal),
  UNIQUE (deployment_id, assertion_id, adjudicator_version),
  UNIQUE (deployment_id, batch_id, ordinal, assertion_id, adjudicator_version),
  FOREIGN KEY (deployment_id, batch_id, adjudicator_version)
    REFERENCES public.relation_apply_batches
      (deployment_id, batch_id, adjudicator_version) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, assertion_id)
    REFERENCES public.normalize_relation_assertions
      (deployment_id, assertion_id) ON DELETE CASCADE
);

CREATE TABLE public.relation_application_receipts (
  deployment_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  batch_id uuid NOT NULL,
  ordinal bigint NOT NULL,
  relation_id uuid NOT NULL,
  identity_outcome text NOT NULL CHECK (identity_outcome IN ('new', 'evidence')),
  input_digest text NOT NULL,
  completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (deployment_id, assertion_id, adjudicator_version),
  FOREIGN KEY (deployment_id, batch_id, ordinal, assertion_id, adjudicator_version)
    REFERENCES public.relation_apply_batch_inputs
      (deployment_id, batch_id, ordinal, assertion_id, adjudicator_version)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, relation_id)
    REFERENCES public.relations (deployment_id, relation_id)
);
CREATE INDEX ix_rel_apply_receipt_fact ON public.relation_application_receipts
  (deployment_id, relation_id);

ALTER TABLE public.relation_adjudications
  ADD COLUMN triggering_assertion_id uuid,
  ADD CONSTRAINT uq_rel_adjudication_deployment
    UNIQUE (deployment_id, adjudication_id);
COMMENT ON COLUMN public.relation_adjudications.triggering_assertion_id IS
  'Logical reference to normalize_relation_assertions; scrub with triggering_claim_id on hard forget.';

CREATE TABLE public.relation_application_adjudications (
  deployment_id uuid NOT NULL,
  assertion_id uuid NOT NULL,
  adjudicator_version text NOT NULL,
  adjudication_id uuid NOT NULL,
  PRIMARY KEY (deployment_id, assertion_id, adjudicator_version, adjudication_id),
  FOREIGN KEY (deployment_id, assertion_id, adjudicator_version)
    REFERENCES public.relation_application_receipts
      (deployment_id, assertion_id, adjudicator_version) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, adjudication_id)
    REFERENCES public.relation_adjudications (deployment_id, adjudication_id)
);
```

`identity_outcome` separates identity from its possible side effects:
`contradict`/`supersede` can produce `new` identity plus several adjudication
rows. Existing `adjudication_outcome` need not be mistaken for this receipt's
two-valued identity result. One triggering assertion may create a new fact,
cap a predecessor, and re-split historical states, so the result junction
records every produced adjudication instead of one lossy UUID array. Add
verdict rows carry the populated `triggering_claim_id` as well.

Do not add a FK from every unapplied membership to an application receipt:
the receipt intentionally does not exist yet. Atomic apply sets all eligible
membership links' `applied_at` only after creating/reusing the receipt. The
barrier verifies a matching complete receipt for every applied input, with
the exact generation. Batch membership cannot be reordered or extended after
admission. Hard forget is an explicit exception: remove source-derived rows,
retain ordinal gaps, and record source removal without replaying those gaps.
Batch expected counts must be reconciled under the same forget/write fence;
otherwise a source deletion would strand the batch. Do not renumber surviving
history when deleting inputs.

### 7.5 Exact fact field and exclusion changes

The final enum is `state | occurrence | unknown`; `undated` describes missing
evidence dates and is **not** a fourth kind. Bound origin is a separate enum.
Add `migrate`/`migration` to the existing adjudication enums in a committed DDL
step before inserting records with those new values.

```sql
ALTER TYPE public.adjudication_outcome ADD VALUE IF NOT EXISTS 'migrate';
ALTER TYPE public.adjudication_method ADD VALUE IF NOT EXISTS 'migration';

CREATE TYPE public.fact_temporal_kind AS ENUM ('state', 'occurrence', 'unknown');
CREATE TYPE public.fact_temporal_basis AS ENUM
  ('world_time', 'verdict', 'source_removed', 'legacy', 'unknown');

ALTER TABLE public.relations
  ADD COLUMN temporal_kind public.fact_temporal_kind NOT NULL DEFAULT 'unknown',
  ADD COLUMN valid_from_basis public.fact_temporal_basis NOT NULL DEFAULT 'unknown',
  ADD COLUMN valid_until_basis public.fact_temporal_basis NOT NULL DEFAULT 'unknown',
  ADD COLUMN seed_claim_id uuid,
  ADD COLUMN occurs_from timestamptz,
  ADD COLUMN occurs_until timestamptz,
  ADD COLUMN occurs_precision public.claim_valid_precision,
  ADD COLUMN temporal_revision bigint NOT NULL DEFAULT 0,
  ADD CONSTRAINT ck_rel_temporal_revision CHECK (temporal_revision >= 0);

ALTER TABLE public.observations
  ADD COLUMN temporal_kind public.fact_temporal_kind NOT NULL DEFAULT 'unknown',
  ADD COLUMN valid_from_basis public.fact_temporal_basis NOT NULL DEFAULT 'unknown',
  ADD COLUMN valid_until_basis public.fact_temporal_basis NOT NULL DEFAULT 'unknown',
  ADD COLUMN seed_claim_id uuid,
  ADD COLUMN occurs_from timestamptz,
  ADD COLUMN occurs_until timestamptz,
  ADD COLUMN occurs_precision public.claim_valid_precision,
  ADD COLUMN temporal_revision bigint NOT NULL DEFAULT 0,
  ADD CONSTRAINT ck_obs_temporal_revision CHECK (temporal_revision >= 0);

COMMENT ON COLUMN public.relations.seed_claim_id IS
  'Logical deployment-scoped claim reference: recorded creator, NULL for unrecoverable legacy provenance or hard forget; never minimum evidence claim.';
COMMENT ON COLUMN public.observations.seed_claim_id IS
  'Logical deployment-scoped claim reference: recorded creator, NULL for unrecoverable legacy provenance or hard forget.';
COMMENT ON COLUMN public.relations.temporal_revision IS
  'Monotonic revision for the fact temporal decision and evidence-derived bounds; changed only by guarded catalog writes, used for correction CAS and cache fencing.';
COMMENT ON COLUMN public.observations.temporal_revision IS
  'Monotonic revision for the fact temporal decision and evidence-derived bounds; changed only by guarded catalog writes, used for correction CAS and cache fencing.';

ALTER TABLE public.relations
  ADD CONSTRAINT ck_rel_state_nonempty CHECK
    (temporal_kind <> 'state' OR valid_from IS NULL OR valid_until IS NULL
      OR valid_until > valid_from) NOT VALID,
  ADD CONSTRAINT ck_rel_occurrence_uncapped CHECK
    (temporal_kind <> 'occurrence' OR valid_until IS NULL) NOT VALID,
  ADD CONSTRAINT ck_rel_occurs_nonempty CHECK
    (occurs_from IS NULL OR occurs_until IS NULL OR occurs_until > occurs_from)
    NOT VALID,
  ADD CONSTRAINT ck_rel_occurs_precision CHECK
    ((occurs_precision IS NULL AND occurs_from IS NULL AND occurs_until IS NULL)
      OR (occurs_precision IS NOT NULL AND occurs_precision <> 'unknown'
          AND occurs_from IS NOT NULL)) NOT VALID;

ALTER TABLE public.observations
  ADD CONSTRAINT ck_obs_state_nonempty CHECK
    (temporal_kind <> 'state' OR valid_from IS NULL OR valid_until IS NULL
      OR valid_until > valid_from) NOT VALID,
  ADD CONSTRAINT ck_obs_occurrence_uncapped CHECK
    (temporal_kind <> 'occurrence' OR valid_until IS NULL) NOT VALID,
  ADD CONSTRAINT ck_obs_occurs_nonempty CHECK
    (occurs_from IS NULL OR occurs_until IS NULL OR occurs_until > occurs_from)
    NOT VALID,
  ADD CONSTRAINT ck_obs_occurs_precision CHECK
    ((occurs_precision IS NULL AND occurs_from IS NULL AND occurs_until IS NULL)
      OR (occurs_precision IS NOT NULL AND occurs_precision <> 'unknown'
          AND occurs_from IS NOT NULL)) NOT VALID;
```

The old table-level `>=` check may remain during conversion: the new state
check is strictly stronger, and retaining the old check still rejects reversed
non-state bounds. Its automatically generated name therefore need not be
guessed or dropped. `occurs_*` stores canonical half-open bounds, so an
instant's end is start + one microsecond; never copy the claim table's
instant-equality check. Occurrence precision is label metadata, not another
identity key. The union reducer must separately bind how mixed granularities
and open evidence select it. The checks above intentionally permit an
unbounded end at coarse precision after union; they do not silently convert
unknown evidence into dated evidence.

`temporal_revision` is an explicit addition beyond D107's current columns,
needed by the correction and cache fencing proposals. Every change of a
consumed temporal input, including an evidence-union change, increments it
atomically. SQL checks alone cannot enforce that only recorded verdicts change
`valid_*`: a CHECK cannot compare old/new rows or inspect adjudication history.
That D107 sentence needs correction. Typed catalog write authority and
transaction contracts enforce this; a database trigger is an alternative
only if its authorization and replay behavior are fully designed.

Before conversion drops the old relation exclusion, hold the deployment
serving/write gate closed. There is exactly one existing relation exclusion
in the inspected migrations; discover its actual PostgreSQL-assigned name
from the catalog and assert that cardinality instead of guessing its truncated
name. No exclusion is added to observations. The final relation exclusion
retains the existing belief-time and contradiction predicates and adds state
kind, so an occurrence cannot be blocked by a neighboring occurrence.

```sql
DO $ddl$
DECLARE
  exclusion_name text;
  exclusion_count integer;
BEGIN
  -- Resolve the one authoritative pre-D107 exclusion, failing on schema drift.
  SELECT count(*), min(conname)
    INTO exclusion_count, exclusion_name
  FROM pg_constraint
  WHERE conrelid = 'public.relations'::regclass AND contype = 'x';
  IF exclusion_count <> 1 THEN
    RAISE EXCEPTION 'Expected one legacy relation exclusion; found %', exclusion_count;
  END IF;
  EXECUTE format('ALTER TABLE public.relations DROP CONSTRAINT %I', exclusion_name);
END
$ddl$;

-- Run the actual resumable conversion while serving and legacy writes remain fenced.
-- After every row is converted and conflicts are resolved by recorded verdicts:
ALTER TABLE public.relations
  ADD CONSTRAINT ex_rel_state_world_window EXCLUDE USING gist (
    deployment_id WITH =,
    subject_entity_id WITH =,
    predicate WITH =,
    object_entity_id WITH =,
    tstzrange(valid_from, valid_until, '[)') WITH &&
  ) WHERE (temporal_kind = 'state'
           AND invalidated_at IS NULL AND contradiction_group IS NULL);

ALTER TABLE public.relations VALIDATE CONSTRAINT ck_rel_state_nonempty;
ALTER TABLE public.relations VALIDATE CONSTRAINT ck_rel_occurrence_uncapped;
ALTER TABLE public.relations VALIDATE CONSTRAINT ck_rel_occurs_nonempty;
ALTER TABLE public.relations VALIDATE CONSTRAINT ck_rel_occurs_precision;
ALTER TABLE public.observations VALIDATE CONSTRAINT ck_obs_state_nonempty;
ALTER TABLE public.observations VALIDATE CONSTRAINT ck_obs_occurrence_uncapped;
ALTER TABLE public.observations VALIDATE CONSTRAINT ck_obs_occurs_nonempty;
ALTER TABLE public.observations VALIDATE CONSTRAINT ck_obs_occurs_precision;
```

This block contains **no conversion implementation**. Adding default `unknown`
fields and successfully validating an empty database does not convert a real
store or establish readiness. The converter must preserve fact IDs, increment
temporal revisions, record migrated provenance, and validate all final
constraints under the deployment generation gate before serving resumes.
Re-running the exclusion replacement blindly is unsupported; migrations own
one-time DDL and the converter owns resumable data work.

### 7.6 Validation performed and remaining proof

On 2026-09-06 the exact SQL blocks in this appendix executed successfully in
a private PostgreSQL 15 cluster initialized with UTF-8 and C locale. The
predecessor schema was extracted without importing application code from the
`_DDL` strings of migrations `p0_02_0001`–`p0_02_0004`. Unavailable and
unrelated `vector`, `pg_textsearch`, and `pg_partman` extension declarations,
plus the `daitch_mokotoff` alias index unavailable in PostgreSQL 15, were omitted.
All referenced predecessor tables and their relevant constraints used their
actual migration definitions. No production or shared database was touched;
the private cluster was stopped after validation.

A transaction rolled back after confirming:

- overlapping same-triple states are rejected;
- adjacent states and two overlapping same-triple occurrences are accepted;
- empty states, capped occurrences and empty canonical instant ranges are
  rejected; a canonical one-microsecond instant is accepted;
- `undated` is rejected as a fact kind;
- two distinct relation assertions from one receipt are accepted and an
  identical tuple is rejected;
- receipt count drift and assertion/receipt generation mismatch are rejected.

This is a syntax, referenced-key, and focused invariant check, **not** a full
head Alembic migration test, a real store conversion, concurrency proof,
permission audit, or scale result. The implementation PR must still execute
the actual migrations through current head on the supported PostgreSQL image,
exercise the full §5 acceptance cases, validate the converter on existing
rows, and verify that no public view/query-role grants expose these private
input journals. The checks that need guarded multi-row writes and complete
barrier membership remain implementation obligations; this appendix does not
claim PostgreSQL foreign keys enforce them by themselves.

## 8. Independent validation of the combined D110 SQL draft

**Checked:** 2026-09-07. **Artifact:**
`plan/designs/temporal_write_and_lifecycle_schema.sql`, exact SHA-256
`f344d26131a9c89760cdc58f1f5cbdfc26066d439449e9776f663e458f28864d`.
This section records evidence for the primary author's synthesis; it does not
approve later edits under the same pathname.

### 8.1 Execution result

The exact combined SQL executes successfully in a new private UTF-8/C-locale
PostgreSQL 15 cluster listening only on its private Unix socket. The predecessor
schema used actual extracted `_DDL` definitions from `p0_02_0001` through
`p0_02_0005`, plus `p7_05_0017_hard_forget.py` for `forget_manifests`.
The same unrelated unavailable extension declarations and old alias fuzzy
index documented in §7.6 were omitted. This supplies actual knowledge-artifact
and forget-manifest composite keys instead of invented stubs.

The positive and eight negative invariant probes from §7.6 also pass against
the combined SQL, including same-triple occurrence overlap, state exclusion,
generation FK mismatch, multi-assertion receipts and unknown-kind naming.
Both test transactions rolled back. The private server was stopped after the
checks; no shared Docker daemon or production database was changed.

**Limit:** this is partial predecessor extraction on PostgreSQL 15. It does
not execute the intervening full Alembic graph, PostgreSQL 19 extension
integration, current catalog grant changes, real conversion, concurrent
writers, hard-forget replay, cache routing, or large-store workloads. A green
DDL run proves syntax and referenced-key compatibility for the extracted
schema, not full-head compatibility or correct cross-row semantics.

### 8.2 Reproduced conversion ordering blocker

The checked draft says STEP D drops the old all-kind relation exclusion only
after STEP C has converted every row. Section 7 of the narrative says converted
tuples are applied in bounded batches during C. Those two requirements cannot
both work for all existing stores.

In a second private database, execute the exact combined SQL **up to but not
including STEP D**, then insert two otherwise ordinary legacy facts with the
same triple and adjacent windows `[2020,2021)` and `[2021,2022)`. They satisfy
the current exclusion. Now classify both as occurrences and clear their old
caps, as D107 requires. PostgreSQL rejects the UPDATE with
`exclusion_violation`: the still-active legacy constraint sees overlapping
`[2020,infinity)` and `[2021,infinity)` intervals even though their new kind
must exempt them.

Required correction: while the serving/legacy-write fence is closed, drop the
legacy exclusion **before the first converted tuple is applied**. Keep final
state-only exclusion creation and all validation after complete conversion.
Alternatively, keep all conversion shadow-only until one atomic final swap
and constraint replacement; that is a different transaction-size/scale
contract and cannot be silently substituted for bounded batch application.
The first option is consistent with the existing bounded conversion design.

The `migrate` and `migration` enum values are currently introduced under STEP B.
Their additions must commit before STEP C inserts migration rows. Moving those
two statements beside the STEP A enum additions would make the required
commit boundary unambiguous. `psql` autocommit in this check supplies the
boundary; a future Alembic implementation must provide it explicitly rather
than inferring it from this successful run.

### 8.3 Shape and authority observations for the primary author

- The draft correctly makes temporal operation sequence come from
  `temporal_blocks` and links relation adjudications to those operations. The
  absence of the earlier analysis's separate batch counter removes a second
  candidate sequence authority. Batch ordinal remains assertion order within
  the closed admitted set; operation sequence records actual intervening
  corrections and other committed mutations.
- Section 3.2 of the narrative now explicitly distinguishes a closed finite
  input set from continuous arrivals and recorded replay. The SQL's active
  batch uniqueness does not secretly strengthen that claim. This addresses
  the principal ordering ambiguity identified in §2.2.
- Applied operation rows currently do not CHECK that `changed_from` and
  `changed_until` agree with changes in the corresponding before/after value
  **or basis**. This is a local tuple invariant that SQL can enforce if those
  flags are exact change descriptors. The typed writer must validate it at
  minimum; otherwise compensation's endpoint-ownership audit can disagree
  with the recorded bounds. Cross-row fact/operation identity, dependency
  acyclicity, support completeness, unit counts and latest revisions remain
  guarded writer obligations, as the narrative correctly acknowledges.
- `temporal_sources.source_id` is UUID-shaped for every kind, while the actual
  predicate registry key is `(deployment_id, predicate text)`. The binding
  contract must name how textual/rule/structural routing keys obtain stable
  IDs, including normalization and collision handling, or persist an explicit
  source-key mapping. No FK or UUID column determines that mapping by itself.
- Checkpoint occurrence precision uses text `unknown` for missing dates;
  fact rows use SQL NULL. Their checkpoint encoder/decoder must explicitly
  map those representations and revalidate canonical bounds. SQL execution
  does not prove that the restored fact tuple is identical to its checkpoint.

### 8.4 Revalidation after the synthesis fixes

**Checked:** 2026-09-07. Exact combined SQL SHA-256:
`cfe1260b9cec7ca47d5a954cd32f1d0995b0b2257ca48b18b32f31167f8668c3`.
The primary author moved `migrate`/`migration` additions into committed STEP A,
moved the legacy exclusion drop to STEP C before converted rows are applied,
left final exclusion creation/validation in STEP D, and added exact endpoint
value-or-basis change-flag checks. The revised schema also adds the source-key
mapping column/uniqueness and makes checkpoint occurrence precision use the
same nullable enum and bound-coherence rule as fact rows.

The exact revised file executes successfully against a newly initialized
private PostgreSQL 15 predecessor database using the extraction and exclusions
described in §8.1. A second private database exercised the corrected conversion
sequence rather than merely running DDL on empty tables:

1. Execute through STEP B, leaving the original relation exclusion installed.
2. Insert two legal adjacent legacy facts with one triple and record their IDs.
3. Execute the revised STEP C exclusion drop, then update those existing rows
   to `occurrence` with uncapped verdict windows.
4. Execute the exact STEP D final exclusion and constraint validations.
5. Verify that both overlapping uncapped occurrence rows survive, their IDs
   match the originals exactly, and the new relation constraints are validated.

All five steps pass. The §8.2 reproduced exclusion-order blocker is resolved
for this snapshot. Three additional probes reject an incorrect false start
change flag, an incorrect true end change flag, and an end **basis-only**
change whose flag remains false. A correctly flagged applied operation is
accepted. The earlier eight negative invariant probes and their positive
counterparts also pass against the revised combined schema.

The private server was stopped after verification. This is still a focused
partial-predecessor PostgreSQL 15 check, **not** a PostgreSQL 19 full-head
Alembic test or an implementation of D107 conversion. The probe's direct
updates intentionally test the repaired constraint ordering; they do not
prove migration-operation recording, policy decisions, full conversion
coverage, readiness, replay, or concurrent writer behavior. Source-key hashing
and checkpoint encoding remain implementation contracts; their declared SQL
shapes and constraints alone do not prove those behaviors.

## 9. Antigravity round-one conversion restart finding

**Independent assessment, 2026-09-07:** Finding 4 in
`/tmp/rs-d110-antigravity-r1.log` correctly identifies an ambiguous restart
instruction, but its proposed `count = 0` no-op is not the simplest safe fix.
Keep constraint-removal DDL strict and execute it exactly once through a
committed Alembic revision; restart the data conversion independently.

The actual migration environment,
`src/rememberstack/spine/migrations/env.py::run_migrations_online`, wraps
`context.run_migrations()` in one `context.begin_transaction()` and does not
set `transaction_per_migration`. Merely placing C and D in separate revision
files therefore does not establish a committed boundary around an asynchronous
conversion. Furthermore,
`src/rememberstack/profiles/selfhost.py::setup` currently calls
`command.upgrade(..., revision="head")`. The implementation must change the
populated-store orchestration, not merely append a migration to that call.

Recommended explicit contract:

1. Commit enum additions before any transaction consumes their new values.
2. Upgrade **to the C schema revision**, checking the expected predecessor and
   original exclusion shape under the closed serving/write fence. Drop the
   legacy exclusion and advance Alembic's revision marker in one transaction.
   Return from that upgrade call so the transaction has committed.
3. Resume data work from `temporal_conversion_runs` and
   `temporal_conversion_rows` under the existing work ledger. This worker never
   re-executes CREATE TYPE, DROP CONSTRAINT, or Alembic stamping. The C marker,
   expected intermediate schema, pinned policy, and active serving fence are
   prerequisites, not inferred from a missing exclusion alone.
4. Only after all expected data has been applied and verified, run the D
   revision. Its precondition checks conversion completeness; final constraint
   creation/validation and the D schema marker commit atomically. An explicit
   zero-row conversion record supplies the same prerequisite for an empty
   deployment.
5. Publish serving readiness only when final schema marker/constraint shape,
   conversion state and fact-generation certificate agree. A restart after D
   but before certification retries certification, not constraint removal.

| Crash point | Authoritative state and recovery |
| --- | --- |
| Before C commit | PostgreSQL rolls back both DROP and schema marker; retry the strict revision. |
| After C commit, during data work | C marker and missing legacy exclusion are expected together; resume only data rows from their durable progress. |
| During D before commit | Final DDL and marker roll back together; verified conversion data remains, so retry D. |
| After D commit | D marker and final partial exclusion agree; C is never invoked again. |

Blind `count = 0` success would hide an accidental/manual constraint drop when
no matching migration marker exists. Blindly rerunning a block that drops
whichever single exclusion exists is worse after D: it can delete the final
state-only exclusion. Cardinality alone is not a complete original-schema
signature. These risks disappear when a recorded schema transition owns the
one-time mutation and drift is rejected. Do not use manual `alembic stamp` to
repair a mismatch without independently verifying the complete schema state.

Alembic's official cookbook describes separate schema/data migration execution,
and its runtime documentation explains explicit transaction/autocommit
boundaries. PostgreSQL transactions atomically commit or roll back their
effects. Sources retrieved 2026-09-07:
[Alembic data migration techniques](https://alembic.sqlalchemy.org/en/latest/cookbook.html#data-migrations-general-techniques),
[Alembic transaction/autocommit API](https://alembic.sqlalchemy.org/en/latest/api/runtime.html#alembic.runtime.migration.MigrationContext.autocommit_block),
[PostgreSQL transactions](https://www.postgresql.org/docs/current/tutorial-transactions.html).
This is a proposed orchestration contract based on inspected code, not a claim
that the existing startup path already implements it.

### 9.1 Validation after the Antigravity and lifecycle fixes

**Checked:** 2026-09-07. Final checked combined SQL SHA-256:
`9025a465ddcbb9d8da49d58ec16205fef27fd4f47ae8b8497478d73741f63fc7`.
This includes the final `relation_application_adjudications` cascade amendment.
The exact file executed successfully against a new private predecessor
database in the PostgreSQL 15 setup described in §8.1. The preceding snapshot
`07913875707e086af114bd6e47daefc93d159a0f394772b84bb4ee964cce1ae1`
also executed, but the evidence below was repeated against the final hash.

The targeted transaction verified:

- Deleting a relation and an observation cascades their discrepancy rows.
  Corresponding historical temporal operations survive with only
  `discrepancy_id` set to NULL; their non-null `deployment_id` remains intact.
- A historical relation application receipt survives deletion of its logical
  fact target, so that receipt no longer blocks exclusive-fact deletion.
- A surviving assertion's adjudication names another relation as its related
  fact. Deleting that adjudication using the related-fact selector cascades
  its application/adjudication junction while preserving the surviving
  assertion's application receipt; deletion of the related fact then succeeds.
- An active batch blocks admission of another batch for the same block even
  at a different adjudicator generation. After the first batch is completed,
  the next-generation batch is admitted.
- Partial preparation identity/fingerprint/snapshot tuples are rejected for
  batch inputs and discrepancies; an output without a preparation is rejected.
  A complete preparation is accepted. An explicit compare-and-swap UPDATE
  stores the first output, a second UPDATE with the empty-output precondition
  changes zero rows, and a stale discrepancy attempt token changes zero rows.
- A read witness preserves its block revision, and a write witness advances
  it by exactly one. The opposite updates are rejected by local checks.
- A newly materialized historical support row marked `unproven` retains the
  default `footprint_complete = false`; the schema does not fabricate a
  complete footprint merely because the row exists.

There are six expected constraint rejections in this targeted probe, plus
the positive deletion, receipt-retention, compare-and-swap and generation
handoff assertions. The prior eight negative invariant checks and positive
counterparts also pass against the final hash. All probe transactions rolled
back, and the private server was stopped after verification.

The revised narrative §6.1 explicitly says that missing historical support is
created as `unproven` with an incomplete footprint, cannot authorize an endpoint
or kind, and must not be promoted merely to satisfy a checkpoint foreign key.
That addresses the invented-support concern at the contract level. The revised
C/D comments bind strict one-time Alembic transitions and separate conversion
retries, consistent with §9's recommendation.

**Limits remain material:** this executes exact design DDL over selected actual
predecessor definitions on PostgreSQL 15, not the complete current Alembic graph
or PostgreSQL 19 stack. The compare-and-swap probes supply the proposed guarded
UPDATE predicates directly; they do not prove a not-yet-written worker uses
them, nor exercise concurrent remote inference. These probes establish the
specific FK deletion paths and local tuple constraints above, not complete
D74 residual erasure, replay support provenance, full read-footprint capture,
cache freshness, migration orchestration, or production readiness.
