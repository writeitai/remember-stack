# Design review — D90 entity-grain observation flush fan-out

**Reviewer:** claude-opus
**Date:** 2026-08-12
**Branch / PR:** `design/d89-entity-obs-flush-fanout` (#262)
**Under review:**
`plan/designs/e3_entity_obs_flush_fanout_design.md` (binding),
`plan/analysis/e3_entity_obs_flush_fanout_analysis.md`,
`decisions.md` §D90,
`plan/designs/e3_claim_level_normalize_fanout_design.md` §5.6 amendment

## Verdict

**REQUEST_CHANGES**

## Summary

The motivation is right and the analysis is sound: D43 is entity-anchored, so
entity-parallel flush is the correct scale-out after D88, and the D84/D88
"advisory lock + complete + anti-join barrier + atomic expected-set insert"
pattern is the right family to copy. The rejected-alternatives table is honest
and the within-entity serial rule is correctly preserved.

The design is not implementable as written because it copies the D84/D88 shape
without re-checking the one property those grains have and entities do **not**:
a chunk and a claim each have a durable structural path back to their document
version, and their work is version-idempotent. A **subject entity is
deployment-global and shared across versions**, and its flush work is *not*
idempotent across versions. Ledger work identity is
`(deployment_id, target_kind, target_id, stage, component_version)`
(`work_ledger.py:967`, `p0_02_0002_infrastructure_registries.py:94`) — it has no
version dimension. So two versions that mention the same entity collide on one
processing row, which silently drops one version's staged assertions and opens
its supersession barrier anyway (B1). The same missing dimension makes the
barrier's own "expected set" and the required entity-grain readiness derivation
unimplementable once staging is drained (B2), and makes the hard-forget payload
scrub (`forget.py:1440-1453`) able to strand a *different* version's barrier
forever (B3). Three smaller binding gaps (cutover coexistence with the
version-wide staging clear, the apply-atomicity change in §5.7, and the
under-specified downstream handoff) are below.

None of this is fatal to the decision — entity-parallel flush is still the right
answer. What is missing is the durable **(version, entity) flush unit** that the
barrier, readiness, forget, and cutover all need to name.

---

## Blocking findings

### B1 — Entity work identity has no version dimension; concurrent versions collide on one row

**Anchors:** design §1.1, §5.2, §5.5; `decisions.md` D90 ¶1;
`src/rememberstack/spine/work_ledger.py:957-971` (`ON CONFLICT (deployment_id,
target_kind, target_id, stage, component_version) DO NOTHING`);
`src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py:94`
(`UNIQUE (deployment_id, target_kind, target_id, stage, component_version)`).

The design pins the primary work unit as `target_kind=entity`,
`target_id=subject_entity_id`, `component_version=OBS_FLUSH_VERSION:entity-fanout-1`.
Nothing in that tuple names the document version, yet the unit of work *is*
`(version, entity, normalizer generation)` — §5.4.2 loads staging by
`(deployment_id, version_id, subject_entity_id, normalizer_version)`.

Subject entities are deployment-global canonical entities (`_BLOCK_ENTITY` reads
`observations WHERE deployment_id AND subject_entity_id`,
`observation_adjudication.py:886-892`) — an entity like a company or a person
appears in most documents that mention it. At BEAM scale, two versions staging
observations for the same entity is the *normal* case, not a corner.

Failure modes, all reachable with two documents ingested minutes apart:

1. **Silent observation loss + premature barrier.** V1 fans out entity E; E's job
   succeeds. V2's claim barrier later inserts E — `ON CONFLICT DO NOTHING`, no
   row created. V2's barrier anti-join (§5.5.3) sees a `succeeded` row for E and
   counts it ready. V2's staged assertions for E are never applied, and
   supersession + embed open on incomplete observation truth. This is exactly
   the "wrong D43 outcome" class.
2. **Wrong slice.** If V1's row is still `pending`, V2 attaches to it, but the
   payload (§5.3) carries V1's `version_id` / `normalizer_version`, so the
   handler loads V1's slice only; V2's rows are never read and never deleted.
3. **Cross-version blast radius.** One `dead_letter` on E blocks *every* version
   sharing E, not just "V's supersession" as §4's table claims.
4. **Cutover / re-run.** After a generation bump, a succeeded row from an older
   version keeps every later version from ever getting an E job.

Note how D88 avoided this for claims: a claim is shared across versions too
(D56), which is why `complete_claim_normalize` carries the
`_VERSIONS_WITH_CLAIM_OCCURRENCE` fan-in loop (`work_ledger.py:348-367`) — but
that only works because normalizing a claim **once** is correct for all versions
containing it. Flushing entity E for V1 is *not* correct for V2. The pattern
cannot be copied without adding the version dimension.

**Required change.** Bind a version-scoped flush unit and state the trade-offs
in the doc (Rule 1: a cold reader must see why). Options, pick and justify one:

- a durable `obs_flush_unit` membership row keyed
  `(deployment_id, version_id, normalizer_version, subject_entity_id)` whose id
  is the ledger `target_id` (new `ProcessingTarget` value, or reuse an existing
  kind with a documented logical FK) — note §5.10's "no new Postgres enum value
  required" is true for *stage* but may not hold for *target kind*;
- a deterministic `uuidv5(version_id, entity_id)` target id plus the membership
  row above so ops can still map a row back to an entity;
- retain staging rows as the membership record with an `applied_at` marker
  instead of deleting them (barrier = "no unapplied rows"), which is
  version-scoped natively.

Whatever is chosen must also state what `ops replay` shows an operator and how a
row maps back to a human-readable entity.

### B2 — Barrier membership and entity-grain readiness have no durable version↔job join path

**Anchors:** design §5.5 ("Expected entity membership for the anti-join is the
set of **processing_state rows** inserted at fan-out … not a live re-DISTINCT of
staging"), §5.9 row 1, §5.11;
`src/rememberstack/spine/readiness.py:228-237` (`_VERSION_WORK` reads only
`target_kind = 'document_version'`), `:241-331` (`_EXTRACT_CHUNK_STATUS`:
version → representation → chunks), `:335-394` (`_NORMALIZE_CLAIM_STATUS`:
version → chunks → `chunk_claims` → claims).

§5.5 is correct that staging cannot be the membership record (it drains as
entities finish) — but then nothing durable ties an entity job to its version.
The only version marker on the row would be `payload->>'version_id'`, which is
(a) unindexed by the §5.11 partial index, (b) a JSON scan across every entity
flush row in the deployment at the exact moment the barrier is hottest, and
(c) **erasable** — see B3.

The readiness consequence is concrete and total. Readiness derives a version's
per-stage status from `_VERSION_WORK` (`document_version` rows only) plus one
bespoke structural derivation per fan-out grain. D84 and D88 could each write
one because chunk and claim have durable joins back to the version. There is no
`version → entity` join in the schema. So §5.9's requirement ("Pipeline
readiness for obs flush generation: entity-grain rows at fan-out version;
version-level success alone insufficient") cannot be implemented as stated, and
until it is, every version reports `adjudicate_observations` as `missing`
forever after cutover — which propagates to connector-cycle and lifecycle waits
(§5.9 row 2, `lifecycle.py:1034-1063`).

**Required change.** Define the durable membership artifact (same decision as
B1), then give the design's binding answer for: the barrier anti-join predicate,
the readiness derivation for a version (including the empty-staging arm, which
must report `succeeded` — compare `_NORMALIZE_CLAIM_STATUS` line 351), and the
index that serves both. Also state the `dead_letter`-blocks and
`missing`-blocks arms in terms of that artifact, since §1.3's "missing entity
rows block" is only detectable if the expected set is recorded independently of
the ledger rows.

### B3 — Hard-forget's payload scrub can strand an unrelated version's barrier forever

**Anchors:** design §5.3 (payload fields required), §5.9 row 3 ("entity
processing rows follow existing processing_state scrub policy — extend if forget
lists stages by grain"), §6 last row;
`src/rememberstack/spine/forget.py:1440-1455`
(`UPDATE processing_state SET payload = NULL … WHERE … target_id = ANY(:entity_ids)
OR target_id = ANY(:resolved_entity_ids) OR payload::text LIKE '%doc_id%'`),
`forget.py:1249-1254` (staging delete by `doc_id`).

Hard forget nulls `payload` for every processing row whose `target_id` is one of
the forgotten document's entities — with no status filter, so `pending` and
`running` rows included. Under D90 those are live entity flush jobs. §5.3 makes
the payload the *only* carrier of `version_id` / `normalizer_version`, and §6
says missing/wrong coordinates must "fail loud" / be non-retryable. So forgetting
document A can dead-letter the flush job for entity E that belongs to unrelated
document B's version — and per §1.3 that permanently blocks B's supersession and
embed. Under the current version-level grain this cannot happen across
documents, because the row's `target_id` is the version being deleted anyway.

**Required change.** Say explicitly how an entity flush job survives the forget
scrub: reconstruct coordinates from the durable membership record (B1/B2) so the
handler needs no payload, or scope the scrub to exclude this stage — and if the
latter, explain why the payload carries no forgettable content. Add the
interaction to §5.9 rather than the current "extend if forget lists stages by
grain", and add a test case (a forget of doc A must not block version B's
barrier).

### B4 — Cutover coexistence is unbound, and the legacy handler wipes other entities' staging

**Anchors:** design §1.6, §5.4.5, §5.8 ("In-flight version-serial flush jobs
finish or are operator dead-lettered + re-enqueued as entity set"), §5.4.3;
`src/rememberstack/workers/e3.py:778-783`
(`self._facts.clear_staged_observations(...)` — version-wide "safety net" drop),
`fact_catalog.py:445-457` vs `:459-477`.

Two things are missing:

1. **The version-wide staging clear must be prohibited in the entity
   generation.** Today's handler ends by dropping *all* residual staging for the
   version/generation. §5.4.5 says an entity job deletes only its own rows, but
   the design never names the existing version-wide clear as something the
   entity path must not inherit — and §9.3 proposes splitting one handler into
   two paths that would naturally share this tail. If it survives into the
   entity path, entity A's job silently deletes entity B's unapplied staging.
2. **Legacy and fan-out rows for the same version may coexist.** A pre-deploy
   `pending` version-serial row for version V is not mutually exclusive with a
   post-deploy claim-barrier re-fire (retry, dead-letter replay) that fans out
   entity jobs for V. Both are then eligible to run. The legacy handler applies
   *all* entities and then clears staging version-wide; concurrently running
   entity jobs then find no rows and, per §5.4.3, **succeed as no-ops** — so the
   barrier opens on work another lease may have only partly finished, and
   double-apply races are handled only by D43's incidental idempotency.

This also weakens §5.4.3 generally: "no rows remain ⇒ success" is only safe if
the owning job is the *sole* deleter of its slice. B3's forget path and the
legacy path both break that premise.

**Required change.** Bind mutual exclusion (no entity fan-out while a
non-terminal legacy flush row exists for that version, and no legacy flush claim
once the fan-out set is materialized), prohibit the version-wide staging clear
in the fan-out generation, and restate §5.4.3's no-op-success arm with its
premise ("the owning job is the only writer that deletes this slice") so the
premise is checkable.

### B5 — §5.7 changes the atomicity grain of D43 apply without stating the consequence

**Anchors:** design §5.7, §12 row 1;
`src/rememberstack/spine/observation_adjudication.py:884`
(`_LOCK_ENTITY = pg_advisory_xact_lock(...)` — **transaction-scoped**),
`:121-182` (`add_observations`: one `engine.begin()` per entity batch, lock +
block read + ordered apply + `clear_staging` in that transaction),
`:180-181` (`_DELETE_OBS_STAGING_ENTITY` — entity-wide, the only staging delete
available).

The reliability goal is right (a multi-hour open transaction across ladder calls
is a real operational problem, analysis §1.4). But the prescribed fix silently
downgrades a guarantee:

- `pg_advisory_xact_lock` is released at commit. "Per-assertion transactions"
  therefore means **the entity lock is released between assertions**. Today one
  entity batch is atomic under one lock; afterwards, a concurrent flush for the
  same entity (another version, continuous ingest — the case D88's
  `_pull_valid_from_earlier` exists for) can interleave *inside* another
  version's ordered sequence. Within-version order survives; batch atomicity
  does not. Whether that is acceptable for open-window caps and contradiction
  grouping is a D43 semantics question the design must answer, not leave
  implicit.
- Partially-applied entity state becomes visible to readers mid-flush. State
  whether that is acceptable and why (readiness gating is the likely answer —
  say so).
- The per-assertion commit shape needs a staging delete keyed by
  `(deployment_id, version_id, subject_entity_id, claim_id, statement)`. Only an
  entity-wide delete exists. §5.7.2 requires the narrow one; the design should
  say it is new (the staging PK already includes `statement`, so it is
  available).
- §5.7 offers two patterns and defers the choice to the implementation PR, while
  §12 repeats it. A choice that determines apply atomicity is design content
  (Rule 2), not an ops knob. Bind one; keep the other as a documented
  alternative with the reason it was not chosen.

### B6 — The ordering key is weaker than today's and would regress determinism

**Anchors:** design §5.4.2, §5.6 row 1 ("Apply in `(asserted_at, claim_id)`
only"); `src/rememberstack/spine/fact_catalog.py:650-660`
(`ORDER BY c.asserted_at NULLS LAST, s.claim_id, s.subject_entity_id,
s.statement`); staging PK includes `statement`
(`p9_08_0029_normalize_claim_fanout.py:31-34`).

One claim can stage several statements for the same subject entity, so
`(asserted_at, claim_id)` does not totally order an entity's slice. Which of two
similar statements from the same claim is applied first decides which becomes
the open observation and which becomes evidence / a contradiction member — so an
untied sort makes retries non-reproducible. The existing query already breaks
the tie on `statement`; the binding design must not narrow it.

Separately, §5.4.2 says "nulls-last **or** documented sentinel". The code has one
behavior (`NULLS LAST`). A binding design should pin it.

**Required change.** Bind `(asserted_at NULLS LAST, claim_id, statement)` in
§5.4.2 and §5.6, and note that undated claims apply last as a documented,
deterministic consequence rather than an open choice.

### B7 — The downstream handoff (supersession payload + embed) is named but not pinned

**Anchors:** design §1.7, §5.2.2, §5.5.4;
`src/rememberstack/workers/e3.py:799-831` (today's flush handler enqueues **both**
`ADJUDICATE_SUPERSESSION` at `ADJUDICATOR_VERSION` **and** `EMBED_CLAIM` at
`P1_EMBED_CLAIMS_VERSION`), `:865-895` (supersession self-loads `relation_ids`
when the payload omits them, given `version_id`, `representation_id`,
`normalizer_version`, `chunker_version`), `:796-798` (`doc_id` falls back to
`claims[0].doc_id`).

"then existing embed follow-up policy" (§5.5.4) and "(and existing embed
follow-up policy)" (§5.2.2) never name the stage, component version, target, or
payload. `adjudicate_supersession` chains `RECONCILE`, **not** `embed_claim` — so
a barrier that enqueues only supersession silently stops claim embedding for
every version, a retrieval regression with no failing job anywhere. Pin it.

Two things the design should record because they make the barrier cheap and a
reader cannot know them otherwise: `relation_ids` may be omitted (the
supersession handler reconstructs it), so `WorkLedger` needs **no** catalog
dependency; and `doc_id` must come from the membership/staging row, since the
ledger has no `claims[0]` fallback.

Also, §1.7 ("exactly as today's empty-observation hop") and §5.2.2 ("enqueue
`adjudicate_supersession` … as today") describe different things: today's
empty-observation hop enqueues a **version-level flush job** that no-ops and then
chains. Pick one and say it once. This matters because four call sites still
enqueue version-level flush at `OBS_FLUSH_VERSION` and would, after the bump,
create `document_version` rows at the **fan-out** component version — a
combination §5.8's and §5.10's dispatch rules do not define:
`work_ledger.py:713-734` (empty extract), `:770-791` (all claims already
succeeded — replay/migration), `workers/e1.py:635-651` (no chunks),
`workers/e2.py:1069-1086` (no chunks).

### B8 — House rule: "v1" framing in binding documents (CLAUDE.md Rule 2)

**Anchors:** design §5.2 ("**Chosen protocol (v1):**"), §5.7 ("**v1 binding:**"),
§8 ("Reject v1" ×2), §12 title;
`plan/designs/e3_claim_level_normalize_fanout_design.md` §5.6 amendment ("the v1
*product* rule … the v1 *implementation* may still use a version-serial lease").

Rule 2 is non-negotiable for design and decision docs: no "v1 / for now / later /
defer / MVP" framing. Most of these are genuine *simplifications* (assertion-grain
jobs are rejected outright; commutative D43 is a documented non-goal) and read
correctly once "v1" is dropped. The D88 amendment paragraph is the worst case —
it describes an implementation phase inside a binding design; rewrite it as
"D88 binds per-entity ordered apply; D90 binds the ledger grain for that flush as
entity-parallel", with no reference to what the implementation used to do.

---

## Non-blocking nits

- **N1 — wrong handler name.** §5.4/§9.3 (and analysis §1.1) call it
  `ObservationFlushHandler`; the class is `AdjudicateObservationsHandler`
  (`workers/e3.py:715`). Cold readers grep for the name they are given.
- **N2 — component version literal.** Current value is
  `"e3-obs-flush-2026.08a:claim-fanout-1"` (`workers/e3.py:64`). "Append
  `:entity-fanout-1`" (§1.1, §9.1) yields a double suffix. State the exact
  resulting string and whether `:claim-fanout-1` is replaced.
- **N3 — index.** §5.11's `(deployment_id, stage, target_kind, component_version,
  status)` mirrors D88's, but the barrier and readiness predicates will need the
  version dimension. Revisit once B1/B2 land.
- **N4 — cost keys.** §7 says "extend `observation_flush:{entity_id}:{index}:…`
  as today"; `add_observations` already appends `:{assertion_index}`
  (`observation_adjudication.py:176`), so nothing needs extending. Worth noting
  that under per-assertion commits the index shifts across attempts as the
  remaining list shrinks, so a `claim_id`/`statement`-derived key attributes cost
  more stably. Ops-only.
- **N5 — Rule 1 vocabulary.** The binding design uses "hub", "residue path",
  "anti-join barrier", "hub_top_k", and "BEAM-scale" without defining them; they
  are only explained in the non-binding analysis. §5.7's "harder; default to
  per-assertion" and §12's table read as notes-to-self. One or two plain-language
  sentences each (what a hub is, what an anti-join barrier does, what BEAM is)
  would make the doc stand alone.
- **N6 — §4 blast radius.** "Entity `dead_letter` on V | V's supersession/embed
  blocked; other entities proceed" understates the shared-entity case; restate
  after B1.
- **N7 — why the pin is complete.** The design asserts the expected set is pinned
  at the claim barrier but never says why that set cannot miss rows. The reason
  is checkable and worth one sentence: each claim job commits its staging writes
  in its own transaction *before* the ledger completes it
  (`workers/base.py:292-307`), so a barrier that requires all claim rows
  `succeeded` necessarily sees all staging.
- **N8 — advisory lock namespace.** §5.5.1/§12 leave "shared vs separate lock"
  open. Recommend binding a distinct namespace (e.g.
  `d90-obs-flush-barrier:<version_id>`) and stating the deadlock analysis rather
  than deferring: `complete_chunk_extract` takes `d84-representation:`
  (`work_ledger.py:1208-1214`), `complete_claim_normalize` takes N
  `d88-normalize-barrier:` locks in sorted order (`:363-367, 1216-1224`), and an
  entity complete would take exactly **one** lock and never nests — so no cycle
  exists in either arrangement, while a *shared* lock would needlessly serialize
  entity completes behind the hot claim barrier. Note also that the D43 entity
  lock is taken in the handler's own transactions, which are closed before
  `complete_*` runs (`workers/base.py:292-307`), so it cannot participate in a
  cycle either. State that; do not leave it to the implementation PR.
- **N9 — test plan gaps.** §10 should add: same entity staged by two concurrent
  versions (B1); forget of an unrelated document during a flush (B3); legacy and
  fan-out rows coexisting for one version (B4); two statements from one claim on
  one entity (B6); embed_claim enqueued exactly once at the barrier (B7).
- **N10 — decisions.md wording.** "Postgres carries O(entities with staged
  observations) processing rows per large version" is only true once the unit is
  version-scoped; restate after B1.

---

## Checklist

| # | Item | Result | Note |
| --- | --- | --- | --- |
| 1 | Expected entity set pin vs live staging DISTINCT after partial flush | **Concern** | §5.5's "membership = rows inserted at fan-out, not a live re-DISTINCT" is the right call, but the rows carry no durable version marker, so the set cannot actually be evaluated per version (B2). Pin *timing* is sound; add the one sentence in N7 explaining why the DISTINCT cannot miss rows. |
| 2 | Empty staging path | **Fail** | §1.7 and §5.2.2 give two different readings ("today's empty-observation hop" = a no-op version flush job vs. "enqueue supersession directly"). Four existing call sites still enqueue version-level flush at the bumped component version — an undefined (target_kind × generation) combination (B7). |
| 3 | Dead_letter / missing entity rows block supersession | **Concern** | The rule is stated correctly, but "missing" is undetectable when membership *is* the set of inserted rows, and blocking crosses versions once rows are shared (B1, B2). |
| 4 | Within-entity order + asserted_at undated claims | **Fail** | `(asserted_at, claim_id)` is not a total order over an entity's slice; today's query also breaks ties on `statement`. NULLS-LAST left as an open choice (B6). |
| 5 | Cross-entity independence (true?) | **Pass** | Verified at the write level: `observations`, `observation_evidence`, `observation_adjudications`, and the staging delete are all keyed by `subject_entity_id`/`deployment_id`, and the candidate block is read per entity (`observation_adjudication.py:886-892, 900-996`). Contradiction groups do not span entities. |
| 6 | Interaction with continuous multi-version ingest and entity advisory locks | **Fail** | The entity lock does serialize same-entity work — but only for jobs that exist. Row-identity collision means the second version's job never exists (B1), and per-assertion commits release the xact-scoped lock between assertions, permitting cross-version interleaving inside an ordered sequence (B5). |
| 7 | `complete_entity_obs_flush` lock ordering vs `complete_claim_normalize` (deadlock) | **Pass (with concern)** | No cycle is reachable: distinct namespaces, entity complete takes exactly one lock, and the D43 entity lock lives in handler transactions that close before `complete_*`. But the design defers the choice to the impl PR instead of stating this analysis (N8), and a *shared* lock would serialize entity completes behind the claim barrier. |
| 8 | Idempotent re-run after partial entity progress | **Concern** | The crash-between-apply-and-complete path is genuinely safe (staging deleted with the apply; retry no-ops). The premise fails where another actor deletes the slice — the legacy version-wide clear (B4) and the forget-by-doc staging delete — so §5.4.3's no-op-success can mask unapplied work. |
| 9 | Legacy version-serial cutover | **Fail** | Coexistence for the same version is not excluded, and the legacy handler's version-wide `clear_staged_observations` is not prohibited in the fan-out generation (B4). Dispatch for a `document_version` row at the *new* component version is undefined (B7). |
| 10 | "No LLM in multi-assert TX" specified enough to implement without re-opening design | **Fail** | The rule is clear; the consequences are not. Missing: xact-scoped lock ⇒ per-assertion lock release and cross-version interleaving; the row-grain staging delete that does not yet exist; and a bound choice between the two commit shapes (B5). |
| 11 | Readiness / lifecycle / forget implications | **Fail** | Readiness has no `version → entity` join to derive from, so §5.9 row 1 is unimplementable as written and every version would report the stage `missing` after cutover (B2). Forget's payload scrub can strand an unrelated version's barrier (B3). |
| 12 | Overclaiming vs under-specifying | **Concern** | Success criteria (§13) are appropriately modest and the "largest hub still bounds the critical path" caveat is honest — no overclaiming. The problem is under-specification at exactly the binding points (membership, downstream handoff, cutover, commit shape), plus Rule 2 "v1" framing and Rule 1 undefined jargon in a binding doc (B8, N5). |

---

## What would make this an approve

1. Name the durable **(version, entity, normalizer generation)** flush unit and
   thread it through fan-out, barrier anti-join, readiness derivation, forget,
   and ops replay (B1, B2, B3).
2. Pin the downstream handoff explicitly — supersession payload fields, the
   `embed_claim` enqueue, and one unambiguous empty-staging rule that covers all
   four existing enqueue sites (B7).
3. Bind cutover mutual exclusion and prohibit the version-wide staging clear in
   the fan-out generation (B4).
4. Bind one commit shape and state what it costs in apply atomicity (B5).
5. Bind the total ordering key (B6) and drop the "v1" framing (B8).

The decision itself — entity-grain leases, serial-in-entity apply, strict barrier
before supersession — should survive all five unchanged.
