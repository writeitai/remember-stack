# Round-3 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES
**Reviewer:** Claude (claude-opus-5)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `3ba0e918`
**Design:** `plan/designs/e3_claim_level_normalize_fanout_design.md` (D88)
**Prior rounds:** `REVIEW_claude-opus_..._impl_2026-08-11.md` (r1),
`REVIEW_codex-sol_..._impl_r2_2026-08-11.md`, `REVIEW_codex-sol_..._impl_r3_2026-08-11.md`

## Summary

The mechanism this round was asked to judge — **lock, fan-out, post-barrier
observations, origin-claim supersession** — is now in place and reads correctly:

- **Fan-out is atomic with the extract handoff.** The complete claim set is
  inserted inside the same transaction that completes the extract barrier
  (`src/rememberstack/spine/work_ledger.py:294-306`, `618-724`), so
  "coordinator done, half the children missing" is unreachable (§5.2).
- **The barrier lock is real and correctly scoped.** `complete_claim_normalize`
  takes a dedicated D88 transaction-scoped advisory lock *before* it marks the
  row succeeded, and holds it across the anti-join and the downstream enqueue
  (`src/rememberstack/spine/work_ledger.py:310-374`, `1118-1126`). The anti-join
  counts `status = 'succeeded'` only — never "any terminal"
  (`src/rememberstack/spine/work_ledger.py:1149-1163`). This closes the
  missed-fire race §5.4 calls mandatory.
- **Observations are staged, then flushed in source-time order.** Claim jobs
  write to `normalize_observation_staging` instead of adjudicating inline
  (`src/rememberstack/workers/e3.py:214-223`,
  `src/rememberstack/spine/fact_catalog.py:401-425`), and the new
  `adjudicate_observations` stage replays them per entity ordered by
  `(claims.asserted_at, claim_id)` (`src/rememberstack/spine/fact_catalog.py:630-640`,
  `src/rememberstack/workers/e3.py:696-737`). That satisfies §5.6's
  apply-in-order contract *within a version*.
- **Supersession no longer trusts a worker-local id list.** The selector is
  rebuilt from origin claims at the normalizer generation
  (`src/rememberstack/spine/fact_catalog.py:459-477`, `651-660`), as §5.5 binds.
- **Readiness and the connector cycle are claim-aware.** Version readiness is
  derived from claim rows with `dead_letter` blocking
  (`src/rememberstack/spine/readiness.py:105-150`, `282-326`), and cycle
  finalization waits on missing/pending/failed/dead-lettered claim rows
  (`src/rememberstack/spine/lifecycle.py:1052-1069`).

All three blocking findings from my r1 review are fixed, and codex's r3 **B4**
(hard-forget staging plaintext) is fixed at this HEAD
(`src/rememberstack/spine/forget.py:1249-1254`, `1557-1561`) — I verified the
catalog contract passes against a real PostgreSQL 16 head (below).

**What blocks merge.** One hard CI break that no unit run can see, one binding
D86 contract silently deleted by this PR, and two open correctness contracts
carried over from codex r3 that are each a one-predicate fix:

1. The branch does not pass its own migration gate — the frozen revision chain
   and catalog inventory in `src/tests/spine/test_migrations.py` were never
   updated for `p9_08_0029` (**B1**).
2. D86's binding observability contract (`e3.claims_processed`,
   `e3.normalize_all_soft_failed`) is deleted, not re-expressed at the new job
   grain, while D88 §5.8 says D86 is "unchanged" (**B2**).
3. Supersession direction is still assigned by processing order, and the new
   selector replaced document order with UUID order, so intra-version
   predecessor/successor is now arbitrary (**B3**, incorporates codex r3 B2).
4. The expected claim set is re-queried from a mutable, generation-unfiltered
   join instead of the extraction generation the handoff fixed (**B4**, codex
   r3 B1).

The deferred two-connection race test is **not** a finding: §5.4 explicitly
defers it and the landed lock is the D84-proven pattern. Codex r3 **B3**
(cross-version D43 ordering) I do **not** treat as blocking — see
"Findings I did not carry forward".

---

## Status of prior findings

| Finding | Round | Status at `3ba0e918` |
| --- | --- | --- |
| Catalog contract constraint counts | r1 B1 | **Fixed** — `EXPECTED_CONSTRAINT_COUNTS` `p: 67` (`src/rememberstack/spine/catalog_contract.py:330`); `verify_schema` passes on a freshly migrated PG 16 head |
| Zero-chunk path never enqueues the flush stage | r1 B2 | **Fixed** — `src/rememberstack/workers/e1.py:629-650`, `src/rememberstack/workers/e2.py:1068-1085` |
| `now()` as derived `finished_at` | r1 B3 | **Fixed** — normalize status COALESCEs to the embed row only (`src/rememberstack/spine/readiness.py:299-302`) |
| Version-level fan-out row runs the serial loop | r1 nit | **Fixed** — coordinator-only rejection (`src/rememberstack/workers/e3.py:137-143`) |
| `OBS_FLUSH_VERSION` duplicated as a literal | r1 nit | **Fixed** — imported in the ledger (`src/rememberstack/spine/work_ledger.py:631-633`) |
| Claim retry skipped work on partial evidence | codex r2 | **Fixed** — the claim path always re-runs (`src/rememberstack/workers/e3.py:177-198`) |
| Connector cycle ignored claim grain | codex r2 | **Fixed** — `src/rememberstack/spine/lifecycle.py:1052-1069` |
| Hard forget left staged plaintext | codex r3 B4 | **Fixed** — `src/rememberstack/spine/forget.py:1249-1254`, `1557-1561` |
| Mutable, unversioned expected claim set | codex r3 B1 | **Open** — B4 below |
| Supersession direction by processing order | codex r3 B2 | **Open** — B3 below |
| Cross-version D43 ordering | codex r3 B3 | **Not carried forward** — see below |
| §11 acceptance matrix uncovered | r1 nit | **Open** — N4 below |

---

## Blocking

### B1 — The branch fails its own migration gate (revision chain + catalog inventory)

`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py`
adds a revision and a table, but the two frozen inventories that guard the
schema chain were not updated:

- `src/tests/spine/test_migrations.py:81` asserts the exact ordered revision
  list, which still ends at `p9_07_0028`.
- `src/tests/spine/test_migrations.py:438` asserts `len(fresh_inventory.tables) == 66`;
  head now has 67 (`normalize_observation_staging`).
- `src/tests/spine/test_migrations.py:465` pins the no-op head to `"p9_07_0028"`.

**Failure scenario:** any CI job that collects `test_migrations.py` fails. The
revision-chain assertion is pure Python — it needs no database and fails in
~1s — so this is not confined to the Postgres jobs:

```text
uv run pytest src/tests/spine/test_migrations.py::test_revision_graph_is_one_linear_structural_chain -q
E   AssertionError: Left contains one more item: 'p9_08_0029'
1 failed in 1.13s
```

With a real database, `test_postgresql_fresh_downgrade_reupgrade_mutation_and_noop_lifecycle`
fails at the table count (`assert 67 == 66`). Note the *contract* itself is
correct — `verify_schema` runs before that assertion and passes, which is how I
confirmed r1 B1 is genuinely fixed. Only the test-side frozen inventories are
stale.

**Fix:** append `p9_08_0029` to the chain tuple, bump 66 → 67, and update the
head pin.

### B2 — D88 deletes D86's binding observability contract instead of re-expressing it

D86's design binds two log events as the denominator for its rate queries:
`plan/designs/e3_unknown_entity_type_gate_design.md:153` ("Log
`e3.claims_processed` once per job"), `:184` ("Denominators (for rate queries):
`claims_processed` per version job"), and `:116` (`e3.normalize_all_soft_failed`).
Both were landed in the D86 impl rounds after being raised as a MAJOR finding.

This PR removes both. At the merge base the serial path counted
`claims_processed` / `soft_claim_errors` and logged them once per job, plus an
ERROR when every claim soft-failed (`git show 5b75a6c9:src/rememberstack/workers/e3.py`
lines 163-219). At `3ba0e918` that accounting is deleted from the serial path
and the new claim path never emits a replacement — `_handle_claim` discards
`_normalize_claim`'s soft-skip return value entirely
(`src/rememberstack/workers/e3.py:186-198`). `grep -rn
"claims_processed\|normalize_all_soft_failed" src/` now returns nothing.

D88 §5.8 states D86 inside claim jobs is "Unchanged". It is not: the gate still
works, but the signal that tells ops the gate is firing is gone.

**Failure scenario:** a prompt or predicate-catalog regression makes the
normalizer soft-drop every claim of a corpus. Every claim job succeeds, the
barrier fires, adjudication and embedding run on an empty basis, readiness
reports ready, and there is no log line anywhere whose rate could show it. Under
the pre-D88 code the version job logged `e3.normalize_all_soft_failed` at ERROR.

**Fix (cheap, and better at the new grain):** log once per claim job — the claim
grain *is* the denominator — e.g. `e3.claim_normalized claim_id=… soft=…`, and
keep an ERROR when a claim job produced neither a relation nor a staged
observation because of a soft drop. Update D86 §8's wording to the claim grain
in the same PR, since the "per version job" phrasing no longer has a referent.

### B3 — Supersession direction: document order replaced by UUID order, and the §5.5 direction rule is still unimplemented

Two related defects, one of them new in this PR.

**(a) New: intra-version adjudication order is now arbitrary.** Before D88 the
adjudication list was built by appending each newly-created relation as its
claim was normalized, and claims were iterated in `(ingested_at, claim_id)` —
i.e. **document order** (`src/rememberstack/workers/e3.py:466-467`, the serial
path). The replacement selector orders by the relation's random UUID:

```sql
-- src/rememberstack/spine/fact_catalog.py:651-659
SELECT DISTINCT e.relation_id FROM relation_evidence e
WHERE … ORDER BY e.relation_id
```

Direction matters because `_adjudicate_pair` always treats the relation being
adjudicated as `new` and every blocked live relation as `old`
(`src/rememberstack/spine/supersession.py:185-193`), then closes `old`'s window
at `new["asserted_at"]` (`src/rememberstack/spine/supersession.py:248-283`).

*Failure scenario:* one document version says "Acme's CEO is Ann" early and
"Acme's CEO is Bo" later. Claims from one version share `asserted_at` (it is the
version's source assertion time — `p0_02_0004_claims_facts_evidence.py:47`), so
`asserted_at` cannot break the tie; document order could, and did. Now whichever
relation drew the lower UUID is adjudicated first and becomes the survivor's
predecessor — a coin flip per ingest.

**(b) Carried over (codex r3 B2): direction is not oriented by source time.**
§5.5 binds: "Direction must follow **asserted_at**, so late older testimony does
not 'win' solely by finishing normalize later." The impl changed which supporting
claim *represents* a relation (`src/rememberstack/spine/supersession.py:401`,
`424`) and the candidate ordering (`:459`) to prefer `asserted_at` — good — but
never compares the pair's assertion times before applying the verdict.

*Failure scenario:* a 2019 document ingested today normalizes after a 2024
document. Its relation is `new`, the 2024 relation is `old`, and a supersede
verdict closes the 2024 window at the 2019 assertion time. The prompt does show
both times (`src/rememberstack/spine/supersession.py:207-214`), so the model
*may* answer `noop` — but the mechanism does not enforce it, and §5.5 is listed
under "Not open".

**Fix:** order the selector by the origin claim's `(asserted_at, ingested_at,
claim_id)` rather than `relation_id`, and in `_adjudicate_pair` swap the
predecessor/successor roles when `old.asserted_at > new.asserted_at` (close the
source-older row at the source-newer assertion time) before applying a supersede
verdict. One test per direction covers both.

### B4 — The expected claim set is not bound to the extraction generation (codex r3 B1, confirmed)

`ExtractChunkBarrier` carries `extractor_version`
(`src/rememberstack/workers/base.py:49-56`), but the fan-out call drops it
(`src/rememberstack/spine/work_ledger.py:294-306`). All three set queries then
select every claim under `(representation_id, chunker_version)` only:

- fan-out membership — `src/rememberstack/spine/work_ledger.py:1128-1137`
- expected count — `src/rememberstack/spine/work_ledger.py:1139-1147`
- ready count — `src/rememberstack/spine/work_ledger.py:1149-1163`
- and readiness repeats the same join — `src/rememberstack/spine/readiness.py:282-326`

`claims.extractor_version` exists (`p0_02_0004_claims_facts_evidence.py:53`) and
a re-extraction writes **new claim rows on the same chunks** (chunks are keyed by
`(representation_id, chunker_version)`, which a extractor bump does not change).
So the set the barrier counts is not the set the handoff materialized — the
binding fixed-set contract of §5.1/§5.2.

**Failure scenario:** version V's fan-out at extractor generation E1 completes
all its claim jobs. A re-extraction at E2 commits new claims for the same chunks
before its own extract barrier fires (or dead-letters one chunk and never
fires). V's barrier now counts E2's claims, finds them unsucceeded, and holds
the downstream chain — observation flush, supersession, embed — on work that
belongs to a different extraction. If the E2 fan-out runs under a newer
normalizer generation, the E1 barrier can never be satisfied at all.

I rate this genuinely blocking but narrower than "permanent stall": in the
common case the E2 extract barrier's own fan-out and in-transaction readiness
check (`src/rememberstack/spine/work_ledger.py:700-724`) re-fires the downstream
and the version self-heals. The point is that whether it heals depends on
timing, which is exactly what §5.1 forbids.

**Fix:** thread `barrier.extractor_version` into the fan-out payload and add
`AND cl.extractor_version = :extractor_version` to the three set queries plus
the readiness query (the readiness side can take the profile's expected
extractor version, which it already resolves at `readiness.py:61-68`).

---

## Non-blocking findings

**N1 — Every claim job loads the representation's whole chunk grid to check one
membership.** `src/rememberstack/workers/e3.py:168-175` calls
`chunks_for_embedding(representation_id, chunker_version)` and builds a set just
to test `claim.chunk_id in chunk_ids`. At BEAM scale that is O(claims × chunks)
rows — 15k claim jobs × a few thousand chunk rows each, with the section join
(`src/rememberstack/spine/chunk_catalog.py:255-270`). D84 already hit this and
added `chunks_for_extract` for exactly this reason
(`src/rememberstack/spine/chunk_catalog.py:101-108`). A single-row
`SELECT 1 FROM claims JOIN chunks … WHERE claim_id = … AND representation_id = …`
is both cheaper and a stronger check. §8 asks for an EXPLAIN of the set queries
at BEAM scale in the impl PR; I see no evidence one was run.

**N2 — Coordinate validation is incomplete and untested.** §5.2 requires the
handler to validate the claim "belongs to representation_id/version_id/doc_id
**for the deployment**", and §11 lists "Cross-tenant payload lie | Rejected".
`claim_for_normalization` looks the claim up by `claim_id` alone
(`src/rememberstack/spine/claim_catalog.py:335-341`) — no deployment predicate —
and `chunks_for_embedding` is likewise deployment-blind, so the membership check
in `src/rememberstack/workers/e3.py:163-175` proves internal consistency of the
payload, not tenancy. `version_id` is never validated against the
representation at all, yet it is the key the staged observations are filed under
(`src/rememberstack/workers/e3.py:217`). Unreachable through the fan-out (which
is deployment-scoped by construction), so this is defence-in-depth — but it is
written into the design and has no test.

**N3 — The barrier keys off a module constant, not the row's own version.**
`_handle_claim` stamps staging and the barrier with `E3_NORMALIZER_VERSION`
(`src/rememberstack/workers/e3.py:222`, `233`) rather than
`work.component_version`. During a mixed-image rollout a worker would complete a
row enqueued at generation A while counting generation B, so the barrier can
never be satisfied for A. §5.7 says roll all normalize workers together, so this
is a nit — but using `work.component_version` removes the footgun for free.

**N4 — The §11 acceptance matrix is still essentially uncovered.**
`src/tests/workers/test_e3_claim_normalize_fanout.py` has four tests, two of
which assert on `inspect.getsource` substrings (`:26-45`) — those prove a string
appears in a function body, not that the mechanism works, and they break on any
refactor. There is no test for: `AdjudicateObservationsHandler.handle` (the
ordered flush, the staging clear, the two follow-ups), the coordinator-only
rejection branch (`src/rememberstack/workers/e3.py:137-143`), the
representation-mismatch rejection (`:172-175`), or the derived readiness status
including the `dead_letter` case (`src/rememberstack/spine/readiness.py:282-326`).
Deferring the two-connection race test is fine (§5.4); these are not that test.

**N5 — Replaying an already-succeeded claim after the barrier orphans its
observations.** A claim job re-run after the version's flush stage succeeded
re-stages assertions (`src/rememberstack/workers/e3.py:214-223`), then
`complete_claim_normalize` re-enqueues `adjudicate_observations`, which is a
no-op because that row already exists and succeeded. The staged rows are never
flushed and never cleared (`clear_staged_observations` only runs inside the
flush handler, `src/rememberstack/workers/e3.py:733-737`). The observations are
silently lost and the plaintext lingers until hard forget. Worth a note in the
runbook at minimum: replay the flush row after replaying a claim.

**N6 — The origin-claim load is duplicated.** The identical chunk → claim →
relation lookup appears in the flush handler
(`src/rememberstack/workers/e3.py:739-750`) and in the supersession handler's
fallback (`:826-849`). One shared helper would keep the §5.5 selector in a
single place; today a change to the selector policy has two edit sites.

**N7 — Fan-out is a row-per-statement loop inside the extract-barrier
transaction.** `_enqueue_claim_normalize_fanout` calls `enqueue_on` per claim
(`src/rememberstack/spine/work_ledger.py:668-700`), and `enqueue_on` issues one
or two statements per row (`src/rememberstack/spine/work_ledger.py:792-840`).
For a 15k-claim document that is 15k–30k round trips in one transaction while
the D84 representation advisory lock is held, blocking every other extract
completion for that representation. §5.2 permits a batched insert and §8 asks
for measurement; a single `INSERT … SELECT` is what §5.2 names first.

**N8 — `EXPECTED_TABLES` insertion is out of alphabetical order.**
`normalize_observation_staging` sits between `mentions` and `merge_events`
(`src/rememberstack/spine/catalog_contract.py:144`). Cosmetic; nothing asserts
sortedness today.

**N9 — The selector's generation filter cannot see re-normalized older
evidence.** `relation_evidence` is inserted `ON CONFLICT (relation_id, claim_id)
DO NOTHING` (`src/rememberstack/spine/fact_catalog.py:509-516`), so
`normalizer_version` is frozen at the first write. A claim re-normalized under a
new generation keeps its old stamp, and
`relation_ids_for_origin_claims(normalizer_version = <new>)` will not return that
relation. Today this is harmless — `_already_adjudicated`
(`src/rememberstack/spine/supersession.py:122-125`) would skip those relations
anyway — but it means §5.5's "or generation match policy" is currently a no-op
that only works for evidence born in the current generation. Worth deciding
explicitly rather than inheriting.

---

## Findings I did not carry forward

**Codex r3 B3 (cross-version D43 observation ordering).** The claim is accurate
as a description of D43: a flush for a 2019 version that runs after a 2024
version's flush still caps the 2024 observation at the incoming assertion time
(`src/rememberstack/spine/observation_adjudication.py:407-448`). But that is
pre-D88 behavior, and D88 does not make it more likely — observations were
always applied per version, in whatever order versions finished. What D88 binds
is §5.6: within a version's flush, apply per entity in `(asserted_at, claim_id)`
order under the entity lock. That is implemented
(`src/rememberstack/spine/fact_catalog.py:630-640`,
`src/rememberstack/workers/e3.py:713-732`). §12 puts the commutative D43
redesign explicitly out of scope. I would not hold the PR for it — but §1.8's
blanket "correctness does not depend on job completion order" is broader than
what ships, so the honest move is a sentence in the design (or a D88 note in
`decisions.md`) recording that cross-version observation ordering remains
completion-ordered until the D43 redesign.

**The `test_e2_chain.py` failure is not a D88 defect.**
`test_empty_extraction_is_terminal_and_replays_without_calls` fails on this
branch, but it also fails at the merge base (`5b75a6c9`) and passes on current
`origin/main`: main fixed the stale D84 assumption in that test
(`d72a3399`-era, driving the CHUNK grain instead of DOCUMENT). The branch is 5
commits behind main. Merge main before landing — main touched only the test in
the files D88 rewrites, so the merge should be clean, but the branch's CI signal
is currently misleading either way.

---

## Verification

Requested unit command, at `3ba0e918`:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_e3_unknown_entity_type_gate.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
→ 34 passed in 4.91s
```

Because the D88 surface is mostly SQL that unit runs cannot execute, I also ran
the Postgres-gated suites against a throwaway PostgreSQL 16.14 container on the
project's own image (migrated to head from base):

```text
src/tests/spine/test_migrations.py                → 2 failed, 4 passed   (B1)
src/tests/spine/test_pipeline_readiness.py
src/tests/spine/test_supersession.py
src/tests/spine/test_observation_adjudication.py
src/tests/spine/test_forget_catalog.py            → 23 passed in 107.95s
```

`verify_schema` passes against the freshly migrated head (it runs inside
`_inventory` before the stale count assertion fails), which is the direct proof
that r1 B1's catalog-contract break is fixed: `EXPECTED_TABLES`,
`EXPECTED_CONSTRAINT_COUNTS`, indexes and views all reconcile with
`p9_08_0029`.

Full `src/tests/workers src/tests/spine` run against the same database:
**FULL_SUITE_RESULT**. The only failure outside `test_migrations.py` is the
pre-existing `test_e2_chain.py` case described above, reproduced at the merge
base and absent on `origin/main`.

## What I would need to flip this to APPROVE

1. B1: three stale assertions in `src/tests/spine/test_migrations.py`.
2. B2: one log line per claim job plus the soft-drop alarm, and D86 §8 updated
   to the claim grain.
3. B3: selector ordered by origin-claim source time, and a source-time swap in
   `_adjudicate_pair`, with a reversed-order test.
4. B4: `extractor_version` threaded through fan-out, both barrier queries, and
   readiness.
5. N4: real handler tests for the flush stage, the coordinator rejection, and
   the derived readiness status (the two-connection race test stays deferred).

The core — lock, atomic fan-out, staged-then-ordered observations, origin-claim
selector — I consider sound and would not ask to be redesigned.
