# Durable observation application under the shared temporal protocol

Status: non-binding analysis, 2026-09-07. This investigates an implementation
contract missing from the accepted D110 schema; it does not accept an amendment.

## The concrete gap

D110 §2 requires observation ingestion to prepare a durable snapshot and attempt
UUID, release locks during model inference, publish the first complete output by
compare-and-swap, and revalidate before atomic application. Current
`src/rememberstack/spine/observation_adjudication.py` instead calls `_ladder`
through `_apply_assertions_locked` inside the transaction opened by
`flush_entity_global_staging`. Its entity lock stays held through model latency.
The same path writes source dates into fact verdicts and bypasses the new journal.
Replacing its date math alone cannot meet the accepted contract.

D90 already supplies the appropriate work units and version barriers. Its
`normalize_observation_staging` rows are keyed by deployment, **version**, claim,
entity, statement and normalizer generation, and are deleted after application.
They have no admission, preparation, recorded answer or completed identity result.
The evidence primary key `(observation_id, claim_id)` makes some repeated support
writes idempotent; it cannot certify the whole assertion decision or remember
which normalized statement from a multi-statement claim produced an effect.

D110's complete `normalize_claim_receipts` is immutable normalization output,
not a mutable observation-adjudication answer. `temporal_discrepancies` requires
an existing fact and represents correction work, so it cannot prepare the first
observation identity. Relation batch inputs and receipts have relation-specific
foreign keys and identity semantics. No existing store legitimately supplies the
missing observation application protocol.

## Independent analysis and alternatives

An independent read-only review traced D90 §§5.1–5.6, migrations 0029/0031,
D110 §2 and incorporated SQL, normalization models and the observation writer.
It independently reached the same gap and recommended extending existing D90
memberships with observation admission/application records. It also identified
a provenance issue that a simple per-claim receipt would miss: one claim can
normalize to several observations, and a later state cap can re-split the
supporting assertions into subsequent slices.

| Alternative | Assessment |
| --- | --- |
| Keep model calls under the entity lock | Contradicts accepted D110 and makes remote latency block every participating writer. |
| Add preparation columns only to disposable staging | Repeats identity decisions across D56 versions, loses completion evidence on deletion, and lacks a closed admitted head. |
| Put mutable answers in normalization receipts | Confuses two distinct decisions and breaks immutable first-complete normalization authority. |
| Use correction discrepancies for insertion | The existing-fact key excludes first insertion and misrepresents identity application as correction work. |
| Generalize relation stores into one generic framework | Viable if both planes demonstrate shared semantics, but currently requires changing accepted relation keys, target cardinality, generation contracts and readers without removing the observation-specific decisions. |
| Copy relation version fan-out and admission wholesale | Duplicates D90's already sufficient work and barrier topology. |
| Preserve D90 units; add observation admission, application and exact assertion support | Reuses the existing scheduler while supplying the missing durable identity and replay boundaries. |

The last choice is the smallest complete candidate. Combining current attempt
and completed application receipt in one assertion-grain row is possible; a
separate normalized-observation assertion table is not necessary if the row
references and validates the exact tuple in the immutable normalization answer.
This is still domain state, not another execution queue: `processing_state`
continues to own leases, attempts, backoff, costs and unit completion.

## Generation and identity challenge

The original D90 unit uniqueness and version-state primary key omit observation
adjudicator generation. Merely adding a string column would let a new generation
reuse an old version barrier. Pin normalizer, semantic observation adjudicator,
and composed flush component generations in version/unit/membership identity.
Changing orchestration alone may reuse an unchanged semantic application receipt;
changing adjudication policy may not. Requiring a new normalizer generation for
every adjudicator change would conflate independent provenance and buy unnecessary
normalization work.

Assertion identity is the exact accepted `(normalized entity, statement)` tuple
within one complete normalization receipt, with a stable UUID. Array position
and claim ID alone are insufficient. Entity redirects change the canonical block
used to apply, not the immutable normalized assertion handle. D56 versions point
to the same assertion plus semantic generation.

## Re-split provenance and remaining contract work

An immutable completed application records what an assertion did at that point
in history. A later cap can legitimately re-materialize a later-world-time state
assertion as another slice. Replacing the original receipt target would rewrite
history; treating the assertion as ordinary new work again would duplicate
application. The later cap's atomic operation group must record the move and
its original assertion provenance.

Current support therefore needs assertion-grain ownership distinct from the
immutable application receipt. The public fact/claim evidence row remains a
projection: remove it only when no surviving normalized assertion still supports
that observation with that claim. Otherwise moving one statement from a claim
would erase another statement's legitimate support. The analysis must settle the
minimum support representation, historical replay of support moves, and forget
closure before binding SQL is written. This does not implicitly extend D112's
relation-only multi-target support rule to observations.

D90 §5.6 also needs a direct textual amendment. A banner saying D110 applies does
not make its detailed permission for lock-held model calls accurate. Closed
admission preserves assertion order; source-removal/correction may interleave
between preparation and application, which then fails exact revalidation.

Acceptance must cover model inference without retained locks, first complete
output CAS, stale preparations, co-present and staggered entity inputs, D56
reuse and generation changes, complete version membership/receipt checks,
multi-statement re-split provenance, group rollback/replay and source erasure.
Legacy metadata at conversion must remain explicitly legacy; new-generation
readiness must never be synthesized from unpinned old unit completion.

## Resolution after the generation and re-split challenge

The independent follow-up confirmed that the keys must include both semantic and
flush generations; a default current-generation string is not historical proof.
It proposed keeping **one mutable current-support location on the application
row**, separately from its immutable original target, instead of adding another
support table. That is sufficient because an observation assertion selects one
identity and a re-split relocates that support; D112 does not authorize multiple
observation identity targets. A reverse current-fact index supplies the closure.

Adopt that smaller representation. Every support move records the assertion and
adjudicator generation, prior/destination fact, prior support owner, establishing
operation and causal cap in a typed versioned payload linked to the ordinary
adjudication/temporal operation group. D56 reuse reads the current location and
original receipt together; it never restores evidence to the original target.
Evidence aggregation checks all remaining assertion support for the same claim
before removing a fact/claim link. Changes in one generation do not silently
drop support belonging to another; rebuild/replacement is explicit lifecycle work.

Several eligible observation candidates do not justify an arbitrary identity.
Keep the unique compatible-state shortcut and grounded single-target semantic
selection; unresolved selection conservatively creates a distinct observation,
which has no relation-style range exclusion. Occurrences always require the
applicable identity judgment, even with identical display text. This clarifies
existing D43/D107 authority without importing relation multi-target semantics.

The recommended amendment adds two domain tables (batches and applications),
one adjudication junction, generation-qualified retained D90 memberships, and
machine-readable support relocation. It removes the need for a separate assertion
or support table and introduces no scheduler. The main cost is per-assertion
history and complete support/re-split validation, which the old evidence PK could
not prove. Operational recovery and deletion follow D110/D74, with historical
unpinned metadata explicitly barred from new-generation completion.


The adversarial draft review found two further gaps and closed them explicitly.
Legacy evidence gains a preserved baseline marker; ordinary new application
support cannot erase it. A cap needing an unrecoverable legacy assertion is
refused with a discrepancy and incoming coexistence, rather than pretending
fresh normalization reconstructed original provenance. D107's mandatory re-split
rule is explicitly qualified by this refusal.

Fact endpoint checkpoints alone cannot restore support assignments after their
move payloads are scrubbed. A support checkpoint extension records each assignment's
independent proof and exact active checkpoint witness. Missing proof produces a
verified terminal erased disposition with no current target/owner. A same-generation
receipt cannot reassociate it; new assertions or a distinct adjudicator generation
can establish independent support. Ordinary temporal corrections do not own
identity. The proposal records automatic same-application reassociation as an
unchosen alternative with its required full authority contract.

## Draft validation and its limits

The independent adversarial review accepted the generation and retirement shape
and found a real NULL-valued CHECK loophole in current-support coherence. The
schema now uses a CASE returning a definite boolean for NULL/linked/erased state.
It also pins an exact active support checkpoint, clears that pointer on a later
ordinary move, and requires D74 reclassification after the last support disappears.
Canceled unapplied admissions may be re-admitted only under a verified checkpoint
whose inventory includes the exact surviving pending assertion/generation/source
membership; completed original receipt coordinates remain immutable.

A private Unix-socket PostgreSQL15 probe executed all **34** D113 statements after
actual D90/D110 predecessor DDL, including populated legacy units, staging and
observation evidence. It verified preserved legacy support, explicit unpinned
metadata without fabricated applications, independent semantic-generation unit
identities, rejection of NULL-state/non-NULL-support and orphan output, rejection
of uncertified erased completion, and normalization-receipt cascade deletion.
The probe exposed a DDL ordering error: dropping defaults on columns added within
the same staging ALTER failed; those default removals now use a separate statement.
Both ASCII and non-ASCII/control-character UUID vectors are pinned in the design.

Probe: `/tmp/rs-t1-d113-schema-pg15.py`; output:
`/tmp/rs-t1-d113-schema-pg15.log`. The fixture uses direct schema rows for the new
normalization/application checks; it does not invent a runtime generation
certificate or claim current observation-writer correctness. The server stopped
in `finally`; shared Docker and production were untouched. PostgreSQL parsing
also accepts all 34 statements. These checks are not full supported PostgreSQL19
migration, application, concurrency, replay or hard-forget acceptance. Antigravity
design review and PR checks remain required before this amendment lands.
