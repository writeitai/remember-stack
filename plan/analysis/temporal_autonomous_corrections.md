# Autonomous temporal corrections: authority, evidence, and safe application

**Status: non-binding analysis.** Prepared 2026-09-06 for D107 spike #366.
This document recommends a contract; it does not amend D107, D108, or permit
WP-T.1 implementation before the design amendments land. The requested product
direction is autonomous, evidence-backed, recorded corrections, with uncertainty
preserving the existing decision and no human queue.

## 1. The problem and the conflicting authorities

Suppose a fact says Alice was CEO from 2020 onward. Another source says she
started in 2019. Merely attaching that claim must not silently replace the
fact's start. The engine must decide whether the testimony concerns the same
tenure, supports a correction, and can coexist with neighbouring tenures.
If accepted, the change needs an explanation and a reproducible history.

Two binding designs currently disagree about who decides:

- `plan/designs/temporal_clocks_design.md` §4.3 requires a human
  `temporal_window` review verdict, says no automatic path changes the window,
  and gives reversals an exception explicitly justified by human authority.
- `plan/designs/unified_remember_distribution_design.md` §3.3 retires
  `remember review` and declares autonomous adjudication the production path.
  `decisions.md` D108 nevertheless says it preserves D107.
- [Issue #366](https://github.com/writeitai/remember-stack/issues/366), inspected
  2026-09-06 through the repository's GitHub CLI, still requires queue linkage,
  reviewer decisions, and a CLI for answering them.

These cannot be reconciled by renaming `reviewer` to `agent`. D107's prohibition
on automatic revisions and D41's retrospective guard must receive an explicit,
narrow amendment. D108 must stop claiming the old human mechanism is preserved.
The useful part of #366 is the guarded, reversible application protocol, not
the queue. The existing `src/rememberstack/spine/review.py` implements legacy
merge and support-withdrawal decisions; it is not an implemented temporal
correction engine.

## 2. Constraints that survive the amendment

| Source | Constraint and consequence |
| --- | --- |
| `decisions.md` D3 | Claims remain immutable testimony. Change the adjudicated fact, never edit all claims to agree with it. |
| `decisions.md` D41, especially its three mechanical properties | Claim windows remain many-valued evidence; one recorded fact verdict is authoritative. Neither a SQL aggregate nor read-time reduction becomes a second validity authority. |
| `decisions.md` D43; `plan/designs/observations_design.md` §3 | Identity and conflict are adjudicated under exact entity blocking. Uncertainty fails toward coexistence, not silent overwrite. Observations retain untyped statements, without new attribute/period registries. |
| `decisions.md` D55; `plan/designs/temporal_clocks_design.md` §4.4 | A living source losing its sole support closes current belief, independently of correction uncertainty. Retained historical testimony is not automatically eligible current support. |
| `plan/designs/temporal_clocks_design.md` §§3, 4.1–4.2 | Temporal kind and seed establish the fact's interpretation. Occurrence bounds are derived metadata; their union must not choose the verdict window or merge occurrences. |
| `plan/designs/temporal_clocks_design.md` §§4.3, 9 | Corrections preserve fact IDs, enforce non-empty states and neighbour bounds, and preserve a recorded before/after trail. Migration is a distinct operation, not a disguised evidence correction. |
| `CLAUDE.md`, Rule 3 | Correctness lives in the library. No cloud UI, operator, or external control plane is required to decide truth. |

The proposed amendment permits the adjudicator to issue an explicit correction
after consulting evidence. It does **not** permit the occurrence aggregate to
write `valid_from`/`valid_until`, or arbitrary model-produced SQL to alter them.

## 3. Recommended decision procedure

### 3.1 Trigger and evidence eligibility

Run correction consideration when evidence attachment, evidence currency,
identity reconciliation, or a correction-policy generation changes a fact's
relevant inputs. A discrepancy includes an earlier supported state start,
a newly dated unknown endpoint, or a potential repair/reversal of a recorded
correction. A timer with unchanged evidence must not repeatedly spend model
budget reconsidering the same question.

For each candidate boundary, require actual eligible claim IDs and the existing
fact-identity verdict linking them to the target. A candidate must derive from
a canonical D41 endpoint of that evidence. The model chooses and explains among
grounded candidates; it cannot invent a date from ingestion time, `asserted_at`,
its own world knowledge, or the convex hull of incompatible testimony.
The ordinary D55 source-removal boundary remains its separately attributed path.

Positive evidence for a new current correction must be non-forgotten and current
under the evidence-lifecycle rules, with its deployment and identity links
validated. Snapshot testimony can remain current even when old; chronological
age alone does not make evidence ineligible. Withdrawn living-source testimony
remains visible as historical context and in `occurs_*`, but cannot by itself
authorize a new correction to current belief. Rejecting a withdrawn proposal
does not delete the record of a correction validly made before withdrawal.

Include contrary evidence and neighbouring slices in the decision inputs.
Independent document lineages matter; re-extractions and duplicate versions are
not additional votes. An evidence count or one confident date is not sufficient
proof that a claim refers to the same tenure or event. Bound the prompt size,
record omitted input counts, and decline to apply a change when required
conflict evidence cannot be evaluated within budget. Do not describe a truncated
comparison as exhaustive.

### 3.2 Deterministic checks versus semantic judgement

Deterministic work should establish canonical endpoints, eligibility, identity
references, temporal relation, legal endpoint movements, and whether the
proposal is already recorded. An unchanged boundary is a recorded no-op if a
decision is necessary; it is not a new mutation.

Use the existing cheap-first adjudication approach for the genuinely semantic
question: does this testimony justify revising this fact's world-time boundary?
For example, “CEO since 2019” might describe the same tenure, an earlier tenure,
or another Alice. The generic condition `evidence.start < fact.start` cannot
answer that. A deterministic semantic acceptance rule is allowed only for a
documented case whose identity and implication are already proven, with tests;
it must never be a blanket earliest-evidence rule.

Persist the evaluated evidence set/fingerprint, candidate endpoint references,
policy/prompt/model generation, model response or deterministic rule ID,
confidence, rationale, and the resulting decision. Use explicit decision
outcomes such as `apply`, `uncertain`, `refused`, and `stale`. Model failure,
invalid output, insufficient margin, or exhausted budget retains the fact's
existing endpoints and records an operational or uncertainty reason.

The confidence margin must be versioned and validated with adversarial examples.
No numeric threshold is established by this analysis. Low confidence is not a
reason to fabricate a resolved outcome, nor to resurrect a human queue.

### 3.3 Allowed mutations

Retain D107's narrow ordinary correction bounds: a known start can move earlier;
an unknown start can become known; a state end can acquire or shorten a finite
bound; a closed end cannot reopen; no change crosses a neighbouring state slice;
and a state with both endpoints known remains non-empty. An occurrence remains
uncapped and the operation does not change temporal kind or fact identity.

If evidence says a known start was too early, record the dispute rather than
silently allowing an arbitrary later start. A compensating reversal can undo an
identified prior correction as described below. Correcting an original seed to
a later start would require a further explicit change to D107's monotonic
contract; general unconstrained editing should not be smuggled in here.

Changed endpoints receive basis `verdict`. Unchanged endpoints preserve their
prior basis. A `legacy` endpoint is not authenticated world time: it may become
`verdict` only through this complete evidence-backed decision, never a cosmetic
basis rewrite. A missing seed stays missing; discovering plausible testimony
does not prove which claim originally created a legacy row.

## 4. Application, concurrency, and replay

### 4.1 One mutation protocol for every temporal writer

The correct unit to serialize is the existing adjudication **block**, not only
the target row. Neighbouring state slices constrain the edit, and a competing
writer can insert a new neighbour without touching the target.

Existing anchors are `SupersessionAdjudicator.adjudicate_new_relation` in
`src/rememberstack/spine/supersession.py` (relation `(subject, predicate)` block
plus shared identity epoch), and `ObservationAdjudicator.flush_entity_global_staging`
in `src/rememberstack/spine/observation_adjudication.py` (entity block before its
staging snapshot). Their present locking orders differ; the implementation must
audit identity, merge/unmerge, reconciliation, and forget writers before adding
a new universal ordering. A correction-only lock cannot protect against writers
that never acquire it.

Recommended target order is the deployment identity/forget fence, then all
affected logical block keys in sorted order, then affected fact rows in stable
`(fact_kind, fact_id)` order. Single-block ingestion retains concurrency between
unrelated blocks. Identity resolution must remain stable from block selection
through commit; an identity change invalidates a prepared correction snapshot.
This order is a proposed cross-writer amendment, not a claim about today's code.

A model call can be prepared outside the write transaction to avoid holding
locks across remote inference. On apply, reacquire the locks and validate the
full snapshot: fact revision, endpoint values and bases, evidence/currency
revision, identity generation, neighbour/block revision, and policy generation.
An endpoint equality check alone misses an intervening change that happened to
return to the same value (the ABA problem). A stale result makes no mutation;
persist its stale outcome and schedule reconsideration for the new fingerprint.

Commit the verdict, updated fact, endpoint revision/ownership, cache invalidation
outbox, and durable work completion atomically. Retrying the same operation ID
returns its stored result. Claim attachment and a correction may be separate
transactions only if the discrepancy work is durably enqueued with attachment.

### 4.2 Ordering cannot depend on model completion timestamps

Replace the old “all caps first, then `(decided_at, verdict_id)`” replay rule.
It cannot reproduce a live history in which a correction preceded a later cap.
Wall-clock timestamps also do not define causal order across workers.

Persist a monotonically ordered operation sequence for each block as operations
commit under its lock. Each operation records its predecessor revisions and
cross-block dependencies. Source units still drain in the agreed D90/#365
staging order; the operation log preserves the resulting adjudication history,
including intervening reconciliation and corrections. Replay that history in
its persisted dependency order, without re-calling a model or drawing new dates
from the database clock. This guarantees repeatability of recorded history; it
does not claim that previously unseen future inputs were globally sorted.

On ordinary retries, the operation ID is already applied or its expected state
is still present. On a rebuild, a mismatching dependency is an integrity or
generation conflict: do not force-apply or silently certify an equivalent
rebuild. Persist a replay conflict and fail readiness for that incomplete
generation. A deliberately changed conversion policy starts a separately
recorded reconciliation/migration path; it does not rewrite historical verdicts.

## 5. Reversal without a privileged human

Reversal is a new **compensating verdict**, never deletion of the original
record. Autonomous authority must be explicit: an evidence-backed adjudication
can conclude that an identified earlier correction is unsupported or concerns
the wrong fact, then propose its compensation. Ordinary source withdrawal is
not sufficient by itself to rewrite history; D55 withdrawal still performs its
independent current-belief action. Hard forget follows its explicit scrub and
recompute contract instead of pretending a removed record never existed in
the operation dependency graph.

Store which endpoint components a verdict changed and the operation that most
recently owned each component. Values alone do not establish ownership.
For each changed component, a reversal may restore the original value **and
basis** only if the current component still has that verdict as its last writer.
Any later cap, correction, or migration affecting that component makes it
ineligible for restoration. Never restore `invalidated_at` through a window
reversal; source removal and other belief-time operations remain in force.

Example: V1 changes Alice's start from 2020 to 2019. V2 later caps her end at
2025. Reversing V1 can restore the 2020 start while preserving the 2025 end.
If V1 also changed the end, that part is skipped because V2 owns it. Persist
the restored and skipped component sets, with reasons. Validate the final
combined tuple and its neighbours before applying anything; an illegal partial
compensation changes no endpoint and records uncertainty/refusal. A reversal
may move a start later or restore an open end only through this tightly scoped
exception, with provenance, current eligibility checks, and no intervening
owner. It never reopens a later cap by accident.

Repeated reversal of the same operation/input fingerprint is idempotent.
Reversing a compensation itself is another evidenced operation with the same
ownership checks, not an unconditional toggle. This explicitly replaces D107's
“human verdict over human verdict” exception rather than claiming D41 already
allows arbitrary autonomous reopening.

## 6. Uncertainty, visibility, and migration

Use durable internal discrepancy/decision records instead of D24 queue items.
The identity is `(deployment, fact_kind, fact_id, input_fingerprint,
policy_generation)`; the fingerprint includes revisions, not just dates.
Store enough state to avoid duplicate inference on replay and to identify the
changed input that makes another attempt worthwhile. Operational retryable
failures are distinct from a completed `uncertain` decision.

Read envelopes expose that a temporal discrepancy remains, its reason, and
available evidence references; they keep the existing authoritative window and
the separately labelled occurrence bounds. No new review CLI is needed. Existing
explain/audit surfaces should report automated decisions and uncertainty, not
tell users to approve a queue.

Migration needs a corresponding amendment to temporal design §9: unresolved
legacy boundaries are machine-readable diagnostics, not work awaiting human
truth decisions. Readiness distinguishes an invalid/incomplete conversion
(must block) from a completed conversion with explicit unknown or legacy bases
(reported uncertainty under its accepted conversion contract). Remove the
requirement for an operator to resolve or accept review items before promotion.

One adjacent defect deserves an explicit resolution: §4.4 currently says a
refused D55 boundary leaves `valid_until = NULL`. If a state already has a finite
end, that would erase a recorded cap. Recommended rule: retain existing endpoints
and bases when a proposed cap is refused, close belief time at the persisted
reconciliation instant, and record the refused boundary. Unknown remains unknown
only when the endpoint was already unknown. This applies independently of whether
the correction adjudicator reaches a decision.

## 7. Alternatives and costs

| Alternative | Advantage | Why it loses / adoption condition |
| --- | --- | --- |
| Keep human temporal reviews | A person can judge ambiguous evidence and authorize broad repairs. | Contradicts the requested autonomous product and D108. Requires an explicit product reversal and supported operator workflow to adopt. |
| Always recompute min/max from claims | Simple implementation and no model calls. | Confuses testimony with authoritative truth, lets disputed or withdrawn testimony rewrite facts, and violates D41. It remains suitable only for labelled occurrence metadata. |
| Freeze every seeded window forever | Cheap, deterministic, and safe from mistaken revisions. | Cannot implement the requested evidence-backed corrections or resolve an unknown start; useful only if the product deliberately withdraws correction capability. |
| Let a model edit any endpoint | Expressive; can repair original seed errors in either direction. | Broader than D107, weakens monotonicity, and can erase succession history. Adoption needs a separately justified general revision contract and stronger evaluation. |
| One deployment-wide temporal lock | Easy serialization proof. | Serializes unrelated entities at million-document scale. Use bounded block locks and explicit cross-block dependencies. |
| Re-run inference during replay | Fewer retained decision artifacts. | Changes historical truth with model/generation nondeterminism; cannot promise D7 replay. |

The recommended design adds an indexed decision/discrepancy record, revisions,
and an operation/dependency log; it also incurs bounded inference only on new
eligible discrepancies. These records are derived source-bearing data, so #368
must cover evidence IDs, rationales, before/after bounds, model payloads, and
dependency artifacts. Storing raw prompt copies without a forget strategy is
not an acceptable shortcut. #367 must consume the same atomic invalidation
outbox used by caps and source removal.

## 8. Required evidence before acceptance and implementation

The binding amendment should update temporal design §§3, 4.3–4.4, 9, 12;
the D24/D41/D107/D108 cross-references; the work-package plan; and the issue's
review/CLI acceptance language. The schema design must define the concrete
record and revision fields. Any text presenting D24 human review as the new
temporal authority must be removed or labelled superseded.

Acceptance requires database-backed tests demonstrating:

1. Eligible evidence causes a recorded earlier-start or unknown-start correction;
   later ingestion time alone, withdrawn-only support, contradictory evidence,
   and insufficient confidence do not.
2. Another tenure's evidence cannot change this tenure; occurrence aggregation
   cannot cap a period fact or merge two occurrence IDs.
3. A neighbour inserted while inference runs makes the proposal stale, even if
   the target endpoints did not change; identity and currency changes do too.
4. An intervening cap defeats stale apply; an endpoint changed and restored to
   the same value still defeats it through its revision.
5. A reversal after a cap restores only its owned start, preserves the cap and
   belief invalidation, restores the proper basis, and refuses illegal tuples.
6. Crash/retry produces one effect and one decision; unchanged uncertainty does
   not call the model repeatedly; changed evidence permits reconsideration.
7. Replay reproduces the persisted sequence, including correction-before-cap,
   without model calls; a missing dependency fails readiness rather than forcing
   an apparently successful rebuild.
8. Unknown-time and refused-boundary D55 withdrawals cannot leave a current
   zombie fact and do not erase a previously finite end.
9. All mutation writers use the same lock ordering; concurrent ingestion,
   correction, identity changes, and forget finish without deadlock or missed
   invalidation, with derived caches refreshed under #367's contract.

These are proofs of the intended behavior, not evidence that it ships today.

## 9. Concrete storage proposal for the binding synthesis

### 9.1 Existing homes and the boundary of the addition

The actual baseline DDL is
`src/rememberstack/spine/migrations/versions/p0_02_0004_claims_facts_evidence.py`,
not just the illustrative schema design. It already has separate `relations`
and `observations`, their evidence-link families, and append-only
`relation_adjudications`/`observation_adjudications` with outcome, method,
confidence, triggering claim, features, generation, actor, timestamp and
supersession linkage. Claims currently have a simple `claim_id` primary key
and `UNIQUE (deployment_id, claim_id)`. `testimony_currency_events` is the
currency authority; `claims.is_current_testimony` is its cache.

Do not create a second narrative verdict table duplicating those adjudication
logs. Extend their outcome vocabulary for temporal corrections/compensations
and record model/rationale/generation there. The proposed `temporal_operations`
below is their typed application receipt: identity, before/after state,
preconditions and replay ordering. Its snapshots are historical witnesses,
never another table that reads consult for current validity. Existing fact rows
remain the sole current window authority. No `review_queue` FK is added.

The SQL below is an exact proposed migration fragment, **not executed DDL**.
It assumes WP-T.1 supplies the fact window basis columns and its kind checks.
DDL CHECK constraints cover local shape; the subsequent application protocol
is required for cross-row semantics. Names may be consolidated with #365's
ordering table during binding synthesis; two block clocks must not survive.

```sql
CREATE TABLE temporal_blocks (
    deployment_id uuid NOT NULL REFERENCES deployments (deployment_id),
    block_key text NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    last_sequence bigint NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    PRIMARY KEY (deployment_id, block_key)
);

CREATE TABLE temporal_discrepancies (
    discrepancy_id uuid PRIMARY KEY,
    deployment_id uuid NOT NULL REFERENCES deployments (deployment_id),
    relation_id uuid,
    observation_id uuid,
    input_fingerprint text NOT NULL,
    policy_generation text NOT NULL,
    state text NOT NULL CHECK (state IN
        ('ready', 'prepared', 'complete', 'retryable', 'superseded')),
    reason_code text NOT NULL,
    prepared_snapshot jsonb,
    prepared_at timestamptz,
    lease_token uuid,
    lease_until timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at timestamptz,
    created_at timestamptz NOT NULL,
    UNIQUE (deployment_id, discrepancy_id),
    CHECK (num_nonnulls(relation_id, observation_id) = 1),
    CHECK ((lease_token IS NULL) = (lease_until IS NULL)),
    FOREIGN KEY (deployment_id, relation_id)
        REFERENCES relations (deployment_id, relation_id),
    FOREIGN KEY (deployment_id, observation_id)
        REFERENCES observations (deployment_id, observation_id)
);
CREATE UNIQUE INDEX ux_temporal_discrepancy_relation
    ON temporal_discrepancies
        (deployment_id, relation_id, input_fingerprint, policy_generation)
    WHERE relation_id IS NOT NULL;
CREATE UNIQUE INDEX ux_temporal_discrepancy_observation
    ON temporal_discrepancies
        (deployment_id, observation_id, input_fingerprint, policy_generation)
    WHERE observation_id IS NOT NULL;
CREATE INDEX ix_temporal_discrepancy_work
    ON temporal_discrepancies (deployment_id, next_attempt_at, discrepancy_id)
    WHERE state IN ('ready', 'retryable', 'prepared');

CREATE TABLE temporal_operations (
    operation_id uuid PRIMARY KEY,
    deployment_id uuid NOT NULL REFERENCES deployments (deployment_id),
    relation_id uuid,
    observation_id uuid,
    discrepancy_id uuid,
    operation_kind text NOT NULL CHECK (operation_kind IN
        ('seed', 'evidence', 'correction', 'compensation', 'cap',
         'source_removal', 'migration', 'identity', 'forget_recompute')),
    result text NOT NULL CHECK (result IN
        ('applied', 'noop', 'uncertain', 'refused', 'stale')),
    expected_revision bigint NOT NULL CHECK (expected_revision >= 0),
    resulting_revision bigint NOT NULL CHECK (resulting_revision >= 0),
    old_valid_from timestamptz,
    old_valid_until timestamptz,
    old_from_basis text NOT NULL,
    old_until_basis text NOT NULL,
    new_valid_from timestamptz,
    new_valid_until timestamptz,
    new_from_basis text NOT NULL,
    new_until_basis text NOT NULL,
    old_invalidated_at timestamptz,
    new_invalidated_at timestamptz,
    old_from_operation_id uuid,
    old_until_operation_id uuid,
    changed_from boolean NOT NULL DEFAULT false,
    changed_until boolean NOT NULL DEFAULT false,
    reverses_operation_id uuid,
    input_fingerprint text NOT NULL,
    identity_generation text NOT NULL,
    policy_generation text NOT NULL,
    reason_code text NOT NULL,
    recorded_at timestamptz NOT NULL,
    UNIQUE (deployment_id, operation_id),
    CHECK (num_nonnulls(relation_id, observation_id) = 1),
    CHECK (old_from_basis IN ('world_time','verdict','source_removed','legacy','unknown')),
    CHECK (old_until_basis IN ('world_time','verdict','source_removed','legacy','unknown')),
    CHECK (new_from_basis IN ('world_time','verdict','source_removed','legacy','unknown')),
    CHECK (new_until_basis IN ('world_time','verdict','source_removed','legacy','unknown')),
    CHECK ((operation_kind = 'compensation') = (reverses_operation_id IS NOT NULL)),
    CHECK (reverses_operation_id IS DISTINCT FROM operation_id),
    CHECK (result = 'applied' OR (
        resulting_revision = expected_revision
        AND old_valid_from IS NOT DISTINCT FROM new_valid_from
        AND old_valid_until IS NOT DISTINCT FROM new_valid_until
        AND old_from_basis = new_from_basis
        AND old_until_basis = new_until_basis
        AND old_invalidated_at IS NOT DISTINCT FROM new_invalidated_at
        AND NOT changed_from AND NOT changed_until)),
    CHECK (result <> 'applied' OR resulting_revision = expected_revision + 1),
    FOREIGN KEY (deployment_id, relation_id)
        REFERENCES relations (deployment_id, relation_id),
    FOREIGN KEY (deployment_id, observation_id)
        REFERENCES observations (deployment_id, observation_id),
    FOREIGN KEY (deployment_id, discrepancy_id)
        REFERENCES temporal_discrepancies (deployment_id, discrepancy_id),
    FOREIGN KEY (deployment_id, reverses_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, old_from_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, old_until_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id)
);
CREATE INDEX ix_temporal_operations_relation
    ON temporal_operations (deployment_id, relation_id, resulting_revision);
CREATE INDEX ix_temporal_operations_observation
    ON temporal_operations (deployment_id, observation_id, resulting_revision);

CREATE TABLE temporal_operation_blocks (
    deployment_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    block_key text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence > 0),
    expected_block_revision bigint NOT NULL CHECK (expected_block_revision >= 0),
    resulting_block_revision bigint NOT NULL CHECK (resulting_block_revision >= 0),
    PRIMARY KEY (deployment_id, operation_id, block_key),
    UNIQUE (deployment_id, block_key, sequence),
    FOREIGN KEY (deployment_id, operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, block_key)
        REFERENCES temporal_blocks (deployment_id, block_key)
);

CREATE TABLE temporal_operation_dependencies (
    deployment_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    predecessor_operation_id uuid NOT NULL,
    PRIMARY KEY (deployment_id, operation_id, predecessor_operation_id),
    CHECK (operation_id <> predecessor_operation_id),
    FOREIGN KEY (deployment_id, operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, predecessor_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id)
);

CREATE TABLE temporal_operation_evidence (
    deployment_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    evidence_role text NOT NULL CHECK (evidence_role IN
        ('support', 'contrary', 'historical', 'candidate_from', 'candidate_until')),
    was_current boolean NOT NULL,
    evidence_fingerprint text NOT NULL,
    PRIMARY KEY (deployment_id, operation_id, claim_id, evidence_role),
    FOREIGN KEY (deployment_id, operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    FOREIGN KEY (deployment_id, claim_id)
        REFERENCES claims (deployment_id, claim_id)
);
CREATE INDEX ix_temporal_evidence_claim
    ON temporal_operation_evidence (deployment_id, claim_id, operation_id);

ALTER TABLE relations
    ADD COLUMN temporal_revision bigint NOT NULL DEFAULT 0 CHECK (temporal_revision >= 0),
    ADD COLUMN from_operation_id uuid,
    ADD COLUMN until_operation_id uuid,
    ADD FOREIGN KEY (deployment_id, from_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    ADD FOREIGN KEY (deployment_id, until_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id);
ALTER TABLE observations
    ADD COLUMN temporal_revision bigint NOT NULL DEFAULT 0 CHECK (temporal_revision >= 0),
    ADD COLUMN from_operation_id uuid,
    ADD COLUMN until_operation_id uuid,
    ADD FOREIGN KEY (deployment_id, from_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id),
    ADD FOREIGN KEY (deployment_id, until_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id);

ALTER TABLE relation_adjudications
    ADD COLUMN temporal_operation_id uuid,
    ADD FOREIGN KEY (deployment_id, temporal_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id);
ALTER TABLE observation_adjudications
    ADD COLUMN temporal_operation_id uuid,
    ADD FOREIGN KEY (deployment_id, temporal_operation_id)
        REFERENCES temporal_operations (deployment_id, operation_id);
CREATE UNIQUE INDEX ux_relation_adjudication_temporal_operation
    ON relation_adjudications (deployment_id, temporal_operation_id)
    WHERE temporal_operation_id IS NOT NULL;
CREATE UNIQUE INDEX ux_observation_adjudication_temporal_operation
    ON observation_adjudications (deployment_id, temporal_operation_id)
    WHERE temporal_operation_id IS NOT NULL;
```

There is deliberately no second `temporal_window_verdicts` narrative authority.
Exactly one matching plane's adjudication row must accompany each operation;
the other plane must not point to it. Apply validates target equality and the
single matching narrative in the same transaction. If the binding design
requires database-only protection from arbitrary application SQL, add a
deferred constraint trigger checking these cross-table invariants; the shown
FKs alone do not establish that bijection. Similarly a fact's endpoint owner
must reference an operation for that exact fact and endpoint, which the writer
checks before commit. No privilege is granted to the open-SQL query role for
these internal tables or mutation functions.

For a new fact, insert its row at revision zero without an endpoint owner,
insert its seed operation and adjudication, then finalize revision one and
owners in that same transaction. Readers cannot see the intermediate state.
For every mutation, `changed_from`/`changed_until` mean value **or basis** changed;
unchanged components preserve their owner. Evidence attachment/currency changes
also advance `temporal_revision` and the affected block revision, even if no
endpoint changes. This closes stale-eligibility races without adding a competing
currency authority. A diagnostic result records the evaluated revision and no
mutation; a stale result's current observed revision belongs in adjudication
features, because its old/new receipt remains the unapplied prepared proposal.

Block rows are counters, not a second work queue. The existing work ledger may
lease discrepancy jobs using their durable IDs; the lease columns above are
an alternative only if that ledger cannot represent this unit. Binding synthesis
must choose one lease owner, not operate both independently. Dependencies are
inserted only from already recorded predecessor operations while holding every
affected block lock; replay rejects cycles, missing predecessors, and gaps in a
retained generation. No database timestamp defines replay order.

These tables are not exempt from D74. The physical claim FKs intentionally make
an incomplete forget fail rather than leave hidden retained links. The worker
must delete/scrub evidence and prepared snapshots first, replace affected
operation history with the approved sanitized checkpoint/recompute outcome,
repair surviving owner/dependency pointers, and only then delete claims. This
is a #368 integration requirement, not permission for unrestricted cascading
deletion of the audit log. The transcript prose, receipt dates, hashes,
block keys, prepared snapshots, and dependencies all enter its inventory.

### 9.2 Bounded preparation and structured semantic output

Preparation captures the exact target window, bases, temporal revision,
endpoint owners, block keys/revisions, identity generation, current evidence
fingerprints, required conflicting/neighbouring facts, and policy generation.
The snapshot must be a versioned typed object; `jsonb` is its serialization,
not permission to accept arbitrary model fields. Persist only a bounded
snapshot. Raw chunk/document content stays in existing provenance stores;
references plus any rendered prompt snippets remain subject to hard forget.

Build a finite candidate list before inference. Each candidate has a stable
`candidate_id`, endpoint kind, canonical timestamp, supporting claim IDs, and
canonicalization generation. A compensation candidate instead references the
prior operation and exact prior component/basis, with its evidence-backed
justification. Candidate IDs are scoped to the prepared snapshot, preventing
a model from borrowing a valid ID from another fact or deployment.

The structured model response contains only:

- `decision`: `correct`, `compensate`, or `uncertain`;
- nullable `from_candidate_id` and `until_candidate_id`;
- nullable `reverses_operation_id` (compensation only);
- supporting and contrary input claim IDs;
- `confidence` in `[0, 1]`, and non-empty `rationale`.

It contains no freely supplied timestamps, SQL, new fact identity, or bases.
Resolve selected IDs back to the prepared canonical values in code. Validate
that evidence references are a subset of the supplied input, every changed
endpoint has a corresponding candidate, and the claimed semantic identity
and direction agree with deterministic guards. Malformed/missing required
fields are `refused` with a typed reason, not coerced into an accepted change.

Preparation must enumerate required conflict constraints before semantic
ranking. Candidate count, input-token count, model-call count and cost are
bounded by the existing write-path budget policy. If all required evidence
cannot fit, record `uncertain: incomplete_comparison`; do not apply a narrow
comparison and claim it covered the full block. Model calls happen after the
prepare transaction ends; all preconditions are revalidated before apply.
One small-model call plus at most one frontier escalation is the existing
ladder's shape, with durable metering call keys derived from discrepancy,
snapshot fingerprint, generation and attempt. Retryable transport failures use
normal work-ledger retry policy; repeated successful uncertainty does not retry
without new inputs or policy.

### 9.3 Reuse thresholds carefully

`SupersessionSettings` in `src/rememberstack/spine/supersession.py` currently
defines `confidence_floor = 0.75`. Its implementation uses the floor to escalate
from small to frontier; it does not independently reject a low-confidence
frontier supersession. Consequently copying that behavior verbatim would fail
the requested conservative correction contract.

`ObservationSettings` in
`src/rememberstack/spine/observation_adjudication.py` defines both
`confidence_floor = 0.75` and `supersede_margin = 0.8`. `_ladder` uses the former
for escalation; `_add_with_block` separately checks `supersede_margin` and
non-empty rationale before permitting a destructive supersession.

Recommend using the existing configured ladder/model choices and confidence
floor for escalation, and the existing configured observation supersede margin
as the starting acceptance margin for temporal corrections on **both** planes.
The effective correction margin is `max(confidence_floor, supersede_margin)`;
persist the resolved values in the policy-generation snapshot. This reuses a
real existing conservative gate rather than inventing a new numeric constant.
It still needs temporal-specific evaluation: neither 0.75 nor 0.8 is a calibrated
probability guarantee. If the small result exceeds the escalation floor but
misses the application margin, one frontier attempt remains permitted before
returning uncertainty. A low-confidence frontier response cannot apply.

Deterministic rejection always wins regardless of confidence. A valid semantic
answer whose inputs changed becomes `stale`, not `refused`; new evidence can
create a new discrepancy. An accepted but unchanged candidate becomes `noop`.
An actual mutation becomes `applied`. The existing adjudication log holds the
semantic response/rationale and gate/coercion reasons; the operation receipt
holds the precise resulting application outcome. This distinction prevents an
optimistic model verdict from being reported as a window change that never
committed.

## 10. Cross-analysis review and recommended consolidation

**Review scope:** this document, `temporal_relation_staging.md` §§1–7, and
`temporal_cache_and_forget.md` §§1–6, inspected 2026-09-06 after the analysis
branch rebase incorporating D108 implementation. This section supersedes the
alternative machinery proposed earlier in this document where stated below;
it remains non-binding input to the primary design synthesis.

The three analyses agree on authority: fact rows determine validity, immutable
claims supply evidence, existing adjudication logs explain decisions, and
derived artifacts can never confer truth. They do **not** yet agree on the
smallest storage/ordering protocol. Concatenating all their DDL would introduce
duplicate revisions, competing work leases, and ambiguous replay order.

### 10.1 One operation history, with assertion receipts as indexes

Keep the existing relation/observation adjudication logs as the semantic
narrative. Keep one shared typed temporal application history for guarded fact
effects and their dependency order. These are two aspects of one recorded
decision, linked and committed together, not two independently writable
authorities.

The relation analysis correctly observes that an assertion may produce several
effects: create a successor, cap a predecessor, add contradiction membership,
or re-split a state. My earlier “one operation per matching adjudication” rule
must therefore apply to each **effect**, not to the whole assertion. An assertion
receipt remains useful as the unique retry/index handle for
`(deployment, assertion_id, adjudicator_version)`. Its contents should be only
closed batch membership, target identity/result, input fingerprint and references
to its recorded effects. Do not put copied bounds, model results, or another
adjudication rationale in the receipt.

Concretely, replace `relation_application_adjudications` with an
assertion-to-operation junction, with stable effect ordinals when several
operations commit together. Each operation then links its existing-plane
adjudication. The assertion receipt is complete only when every referenced
effect, affected fact, evidence link, outbox event and membership update commits
atomically. A subsequent correction is a new operation referencing the prior
effects; it does not rewrite the assertion's original identity receipt.
This removes the opportunity for a receipt's copied verdict to disagree with
the temporal history while retaining the necessary multi-output retry guarantee.

An unsuccessful semantic comparison may have no target mutation. Its existing
adjudication is still a transcript, but does not require inventing a fake
`applied` temporal effect. Uncertain/refused/stale correction receipts can retain
their actual inspected target, with no revision increment as §9 specifies.

### 10.2 One block sequence; closed batches do not supply a second clock

Use a single block mutation sequencer for relation apply, corrections,
compensations, reconciliation and migration. Observation blocks use the same
mechanism under their distinct entity-block key. `temporal_blocks` and
`temporal_operation_blocks` already propose this. Do not separately increment
`relation_apply_batches.batch_no` and later guess how it interleaves with a
correction sequence.

The simpler resolution is to remove `batch_no` as an authority. Keep a closed
batch UUID, exact immutable input membership, and ordinal within that batch.
Allow only one active relation batch per canonical subject/predicate block,
independent of adjudicator generation; drain or retire it before admitting the
next. Its operations receive the shared block sequence when they commit. A
batch with no committed operation has no world-state effect whose order must
be replayed. Its admission membership is still durable and resumable. Completed
batches can be ordered by their recorded operation ranges when needed for
diagnostics; their admission timestamps never define truth or replay.

Corrections can commit between assertion ordinals, because their effects are
recorded in the same stream and a prepared assertion then revalidates. The next
assertion cannot overtake an unfinished prior admitted assertion. Keep the
relation analysis's honest limitation: closed available inputs have a defined
order; unseen later versions do not retroactively change the immutable seed.
A rebuild reproduces persisted admission/operation history, not an imagined
global sort of testimony that had not arrived.

### 10.3 Use one prepare/revalidate protocol and one scheduler

The relation analysis §3.3 favors a dedicated session advisory lock across a
whole admitted batch and provider calls; this document and the cache analysis
favor short snapshots and locked revalidation. Both can be correct, but retaining
both as binding options leaves the cross-writer contract undecided. Long session
locks across unbounded hot-block batches also delay correction and ordinary
reconciliation and require a separate connection lifecycle.

Recommend the uniform short-transaction prepare/revalidate protocol. Freeze the
closed batch once, persist its next unapplied ordinal, prepare that ordinal
under the shared block locks, release locks during inference, and validate block,
identity, source/fact revisions before atomic apply. The ordinary work ledger
owns the unit lease. Another worker may continue the same ordinal after lease
loss, but the receipt idempotency key and snapshot validation ensure one effect.
Do not accept the next ordinal until the previous one is durably resolved.
Reconciliation and corrections can intervene; stale preparation is a recorded
outcome, followed by preparation against the new revision.

Choose the cache analysis's existing `processing_state` scheduler for **all**
new temporal work. Remove `lease_token`, `lease_until`, `attempt_count`, and
`next_attempt_at` from my proposed discrepancy DDL, and remove the alternative
custom lease language. Discrepancy state is semantic progress (`prepared`,
completed result, superseded input), not a second status/attempt database.
Use its stable discrepancy ID as the work target; register a real correction
component/stage, and enqueue on the authority writer's transaction. Boundary
events keep their event IDs and `not_before` in the same ledger. Do not encode
event identity into component generations or revive a previously succeeded
work row by overwriting it.

The common mutation lock order must be explicit: existing deployment forget
fence, identity epoch, sorted logical adjudication blocks, sorted fact rows,
sorted temporal routing/source keys. Barrier/processing completion locks must
not be acquired while retaining locks in an inverted order. The relation analysis
identifies actual block-before-identity code that must change with this contract;
writing the order in a document alone does not make the implementation obey it.

### 10.4 One fact revision, separate derived routing certificates

Both the relation analysis §7.5 and my §9 add `temporal_revision` to each fact.
Create it exactly once. It advances for all consumed inputs: window/basis,
temporal kind, evidence membership/currency, identity and derived occurrence
changes. The cache analysis's fact-leaf `temporal_sources.revision` must be an
atomic mirror of that value, not a second independently incremented fact clock.
Its entity/predicate/structural aggregate-key revisions remain distinct: they
fence sets whose membership changes even when an artifact consumed no facts.

A boundary passing changes current eligibility but need not mutate a fact's
world-time fields or increment the fact's evidence revision. Cache workers can
advance affected aggregate source-key revisions and certification state using
the recorded fact revision and evaluation instant. If a fact-leaf source needs
a separate routing tick, name and define that field as derived routing state;
do not silently advance the mirror beyond the fact's revision. This distinction
prevents a clock refresh from making an otherwise unchanged correction appear
to depend on nonexistent new evidence.

The cache analysis's membership/index tables are justified when they avoid
scanning broad rule sets to discover the next boundary. They do not justify
another writer, queue, or generalized graph of source revisions. Retain the
listed rule-to-source matrix and consumer inventory: checking only profile
hydration does not stop stale vectors from affecting nomination or clustering.

### 10.5 Evidence eligibility, reversal and forget are different operations

Keep these three rules separate in the synthesis:

1. A **new current correction** requires current, non-forgotten admissible
   testimony and a positive identity/temporal judgement. Withdrawn testimony may
   explain history; its inclusion in `occurs_*` is not permission to update
   today's verdict.
2. A **compensation** reverses an identified recorded correction with fresh
   evidence-backed justification and endpoint ownership checks. A later cap or
   belief invalidation survives. Simple support withdrawal invokes D55 and does
   not automatically undo a historically valid correction.
3. **Hard forget** removes source-derived information, including historical
   before/after snapshots and rejected candidates. The cache analysis correctly
   says an unsupported endpoint must not survive just because it is “audit.”
   This is an explicit erasure exception to normal monotonic window rules.

For surviving facts, forget first classifies each endpoint's independent retained
justifications. Preserve an existing endpoint only when an admissible surviving
justification establishes it independently; this includes already recorded valid
historical justifications, not an invented new correction from a withdrawn source.
Clear unsupported endpoint/basis to unknown under a recorded erasure operation,
preserving independently justified other components and D55 belief closure.
Any genuinely new semantic choice must pass the autonomous decision protocol;
if uncertain, clear the erased information rather than retaining it while a model
or operator is undecided. Forget must not wait for human truth approval.

Replaying old operations after deleting their dependencies is incompatible with
the normal “missing dependency fails readiness” rule. The explicit resolution is
a **sanitized checkpoint under the existing forget fence**: replace affected
historical dependency roots with a recorded clean surviving state, its retained
justifications, content-free erased operation identities, and a verified replay
frontier. Scrub dates, rationales, prompts and rejected candidates from all
affected snapshots. Rebase surviving endpoint ownership/dependencies onto the
checkpoint and retain only operations whose preconditions remain meaningful.
Replay starts from that checkpoint; it never force-applies an operation whose
evidence was erased. Missing dependencies without such a verified checkpoint
still fail readiness. Do not add a new portable forget log: extend the existing
manifest as the cache analysis specifies.

Checkpoint schema and retention must be binding before code, not the vague
“scrub or delete” alternative in the earlier DDL. My §9 FKs are intentionally
restrictive; simply deleting referenced operations will fail. The binding schema
must explicitly support sanitized operation/checkpoint roots and ordered pointer
repair, with tests for shared survivors and later independent caps. Keeping
`old_*` as non-null basis fields is compatible with a sanitized unknown snapshot,
but such a rewritten receipt must be marked erased/checkpointed and excluded
from ordinary replay; it cannot pretend to remain its original adjudication.

### 10.6 Exact D107 contradiction register for the amendment

| Binding location | Required resolution |
| --- | --- |
| Temporal design §3, window-writer table | Replace human-only review with explicit autonomous correction, compensation and erasure paths; retain separation from occurrence aggregation. |
| §3, “database check forbids” derived fields writing verdicts | Replace the impossible CHECK claim with guarded catalog/transaction enforcement; DDL shape checks are not authorization. |
| §4.2, retry key | Use normalized assertion identity plus generation: one claim can produce several valid relation assertions. |
| §4.2, undated relation attachment | Use the defined `state / occurrence / unknown` kind and the normalizer's shape judgement; do not force all undated relations into a state. |
| §4.3, human queue, actor, reversal exception | Replace with the autonomous contract, recorded inputs/generation, uncertainty, endpoint ownership and grounded compensation authority. D24/D41/D107/D108 references must agree. |
| §4.3, caps-first timestamp replay | Use the one persisted causal operation stream; timestamps are audit only. |
| §4.4, refused D55 cap | Preserve existing endpoints/bases; close belief time at the persisted reconciliation instant; unknown remains unknown only where already unknown. |
| §4.4, cap refusal “sent to review” | Record diagnostic/uncertainty and schedule eligible autonomous consideration; no human queue and no unrecorded force-cap. |
| §7.1 versus §12 cache evaluation | Due boundaries nominate refresh; delayed work rebuilds at current evaluation time and certifies its actual next deadline. Every current consumer checks freshness. |
| §9, `undated` enum | Use `unknown`; distinguish missing dates from shape. |
| §9, operator resolves/accepts items before promotion | Readiness gates conversion validity/completion; completed explicitly unknown cases are reported without operator truth adjudication. |
| §9/§12, replay after erasure | Describe verified sanitized checkpoint roots, retained IDs and dependency repair; ordinary missing dependencies remain an error. |
| §12, four unwritten contracts | Replace with links to accepted amendments only once independently reviewed and binding. The analysis documents alone do not clear T.1's gates. |

This consolidation keeps necessary evidence, ordering, cache and erasure
machinery while removing duplicate clocks, queues and narrative verdicts.
Its strongest unresolved item is the exact sanitized-checkpoint schema and
replay proof. The primary amendment must settle that mechanism explicitly;
it must not cite this analysis as if the proof already exists.

## 11. Independent review of the D110 binding draft (2026-09-07)

Reviewed the actual proposed `temporal_write_and_lifecycle_design.md`, its full
SQL appendix, amended `temporal_clocks_design.md`, `hard_forget_design.md`,
`decisions.md` D110 and the temporal work-package plan. These findings concern
that draft, not the earlier independent DDL sketches. No binding file was
changed in this review. Syntax/execution probes reported by the primary author
are useful structural evidence but cannot establish concurrent writer semantics.

### R1 — block acceptance on an immutable prepared-output binding

Draft §2 records preparation and then a successful model output before apply.
`relation_apply_batch_inputs` and `temporal_discrepancies` each store mutable
`prepared_snapshot`/`prepared_output`, with only the shape check that output
requires a snapshot. The text permits a helper to prepare the same head but
does not say how output publication pins the exact snapshot evaluated.

Concrete race: A prepares revision 1; evidence changes; B replaces the stored
snapshot with revision 2; A returns and stores its revision-1 model answer beside
the revision-2 snapshot. Apply's revision-2 checks can now succeed despite the
model having evaluated different evidence. An application receipt's uniqueness
prevents double application, not this wrong-input application.

Required resolution: each preparation has an immutable attempt token and input
fingerprint; provider output publication performs compare-and-swap against that
token/fingerprint and accepts only the first complete result. A successful result
must never be detached from its snapshot. Repreparation retires the old pair and
creates a new token; late outputs fail closed. Apply validates the persisted
output's preparation token as well as current authority revisions. These can be
typed fields within the existing records; a new queue or inference subsystem
is unnecessary. Add the delayed-A/new-B race and concurrent-output winner case
to acceptance.

### R2 — block acceptance on one frontier across generations

Draft §3.2 says one active batch per block/**generation**; SQL's
`uq_rel_active_batch` includes `adjudicator_version`. The head-of-batch restriction
therefore permits generation A's ordinal 1 and generation B's ordinal 1 to be
simultaneously eligible for the same canonical block. Their immutable seed can
again depend on which worker reacquires the block first.

Required resolution: uniqueness for active batches is canonical block alone;
generation remains a pinned property of that batch. A roll must drain or
explicitly retire the previous head before admitting another generation.
Alternatively the design would need another cross-generation frontier, which
is needless machinery when the one-block rule already solves the problem.
Verify with two generations containing conflicting initial assertions, not only
two workers on one generation.

### R3 — clarify same-transaction operation dependencies

Draft §2 requires predecessors to be “already-committed,” while §§2/3.3 require
all effects from one assertion to commit atomically. A later effect in that
transaction can depend on an earlier effect which is recorded but not yet
committed. For example an assertion can create a successor and use its boundary
to cap another fact. Satisfying the literal wording by committing between effects
would violate the atomicity contract.

Permit a predecessor that is either committed earlier or an earlier recorded
effect of the same atomic assertion group. Existing receipt/adjudication links
identify the group; preserve a stable effect order and validate that the graph
is acyclic. Replay must respect that group under the fenced rebuild contract
and must not expose a half-applied group. This is a precise wording/application
contract fix, not a request for another operation log.

### R4 — spell out complete read footprints in the shared journal

The checkpoint algorithm §6.2 expands across dependency/**read-footprint** edges,
but §2 describes sequence membership only for “affected” blocks. A block can be
read to rule out a neighbour without receiving a fact mutation. Its absence
result still influences the decision and is relevant to checkpoint closure.

State explicitly that prepare snapshots enumerate all consumed read and write
block keys, including empty candidate blocks; operation membership/dependencies
persist their observed revisions/heads and a completeness attestation. Only
write blocks advance the mutation revision, while every recorded membership can
advance its ordering sequence as defined by the one sequencer. If historical
inputs lack this footprint, the documented conservative checkpoint fallback
remains valid. New writers should not silently rely on that fallback instead of
recording the footprint their own preparation already knows.

### Findings that do not warrant additional machinery

The draft's assertion receipt → adjudication → temporal operation linkage is
adequate for one shared effect history when the writer checks matching target
and complete membership atomically. A second assertion-to-operation junction
would duplicate those edges without adding authority. The existing operation
support and component attestations are also sufficient to express conservative
independent support; there is no need to add separate endpoint claim tables
merely because this earlier analysis explored them.

Erased bounds are explicitly excluded from confident current membership in
amended D107 §7.1 and D110 §6.1, with uncertain count/absence disclosure. The
checkpoint explicitly fences the deployment, retains clean roots, covers old
prefixes, repairs endpoint ownership, and distinguishes verified database state
from completed all-store forget. Those are coherent design decisions. Tests
must still prove repeated forget and restore against actual lineage/source
scrub, including old root payloads and FK deletion ordering; DDL parsing is not
that proof.
