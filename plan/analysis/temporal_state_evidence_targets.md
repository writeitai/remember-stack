# A state assertion supporting several existing slices

Status: non-binding analysis, 2026-09-07. This is a separate question from
D111's accepted unknown-start exclusion amendment.

## Concrete failure and authority

The store has two same-triple state identities, `[2010, 2013)` and
`[2018, 2020)`. A newly extracted same-value state claim covers `[2010, 2020)`.
D107 §4.2 says overlapping same-value states attach as evidence. D110 §3.3 and
`temporal_write_and_lifecycle_schema.sql`'s application receipt require one
selected fact. Neither earliest UUID, earliest date nor greatest overlap proves
which historical slice this testimony should support exclusively. A model that
returns ordinary coexistence cannot insert the broad new state because the
known-start exclusion correctly rejects overlap. D111 does not change that.

The existing evidence schema already permits a claim to support multiple
relations: `src/rememberstack/spine/migrations/versions/p0_02_0004_claims_facts_evidence.py`
defines `relation_evidence` with `(relation_id, claim_id)` identity. Multiple
support links do not require merging the supported fact identities. The narrow
conflict is D110's scalar application-target authority, not the evidence table.

## Independent alternatives and challenge

An independent read-only analysis initially recommended a completed uncertain
receipt: preserve testimony without materializing a fact, and reconsider when
evidence changes. The first apparent cost was a nullable target and an outcome
CHECK. Examining the full lifecycle changed that assessment. Batch inputs and
receipts are unique on assertion plus adjudicator generation; work-ledger identity
does not include content hash. A changed hash cannot create a new completed work
attempt. Autonomous reconsideration therefore needs immutable per-fingerprint
attempt identities, indexed dependencies, enqueue-on-change participation,
later application ordering and a terminal successful-identity guard. Merely
overwriting the receipt or requiring a deployment-wide policy roll is not a
complete correction mechanism.

| Alternative | Cost and consequence |
| --- | --- |
| Arbitrarily pick one historical slice | Deterministic but unsupported identity selection; drops legitimate support for other compatible slices. |
| Merge slices or extend one across the gap | Violates preserved identity and verdict boundaries; invents continuity without an adjudication. |
| Mark contradiction merely to insert a new row | Turns identity uncertainty into false semantic disagreement. |
| Permit explicit uncertain overlapping fact rows | Adds exclusion exceptions, authority and all consumer/replay/forget transitions; broadens the known-start invariant. |
| Complete uncertain testimony with autonomous reconsideration | Viable full design, but needs attempt/dependency stores and guarded reapplication through the existing ledger. |
| Attach evidence to every compatible overlapping state slice | Reuses existing many-to-many evidence; changes receipt target authority to a set; preserves each slice's identity and verdict. |

For exact same-value state overlap, the last option removes an unnecessary
choice. The normalized claim asserts that value during its own interval and
therefore supports the same value in each overlapping stored slice. It does not
prove that the slices are one episode or that their intervening gap is true.
This is a state-support rule, not generic multi-target occurrence identity.

## Recommended contracts and costs

For a dated state assertion, deterministic targets are all non-invalidated,
non-erased, same-canonical-triple state slices whose known-start verdict windows
overlap the assertion's canonical window. D107's exact compatible-state rule
applies to the complete set. Occurrences remain one model-selected identity;
mixed datedness, different values, erased endpoints and uncertain semantic
matches cannot use this rule. One eligible undated state still follows the
existing undated shortcut; this rule does not authorize arbitrary choices among
ambiguous undated identities.

Replace the receipt's scalar target as authority with a normalized target table:
application key plus relation UUID, a parent-receipt cascade and reverse fact
index. `new` requires one target, `evidence` one or more. The transaction records
all targets and every evidence/journal effect atomically. A target is an existing
or newly materialized fact, distinct from another fact merely capped or grouped
by the same application. Replay reads this exact set without recomputing it.

The complete deterministic target set cannot be reduced to a model's top-k
budget. Enumerate it under D110 block stability, acquire complete sorted fact
locks and stream large evidence/target writes within the one application group.
Remote inference remains bounded and outside locks; its optional semantic
effects must not erase deterministic support. Additional succession effects name
the exact successor fact whose authoritative start supplies the boundary. When
several supported state slices exist, the assertion's broader raw date or an
arbitrary target's start cannot silently choose that boundary. A missing or
unsupported boundary records a refusal without undoing valid evidence support.

All verdict endpoints, seeds and fact IDs stay unchanged by support attachment.
Occurrence metadata may expand independently and must remain labeled as testimony
coverage. Reads and profiles must not infer continuous state across gaps from
that expanded metadata or one claim supporting several identities. Counts remain
fact-identity counts, and evidence counts remain distinct document lineages.

Hard forget includes target rows through assertion/receipt cascade and the
existing closure. Historical logical fact handles follow D110's non-resurrection
rule. Deleting a target cannot make replay attach the old claim to a different
newly nominated fact. Queue/version completion requires a complete nonempty
target set with the proper cardinality; zero targets is not an empty-success
escape.

This adds one internal table and changes result/receipt consumers, journal
application, replay, forget and tests. It does not require a new scheduler,
identity-attempt lifecycle or public SQL grant. Work and locks scale with the
number of compatible slices; that cost is explicit and cannot be hidden by
silently truncating support. Complete block/revision participation remains a
prerequisite.

Acceptance must cover broad-window support of disjoint slices, preservation of
the gap/verdicts/seeds, additional semantic candidates with explicit boundary
authority, low-confidence/omitted model answers preserving deterministic support,
rollback after the second target, concurrent helpers, replay, D56 completion,
source erasure, missing target corruption, and unchanged single-target occurrence
identity. No design or implementation acceptance is established by this analysis.
