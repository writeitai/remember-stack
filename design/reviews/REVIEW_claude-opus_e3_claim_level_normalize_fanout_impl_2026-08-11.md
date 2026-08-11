# Implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES
**Reviewer:** Claude (claude-opus-5)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `ce89b4c5`
**Design:** `plan/designs/e3_claim_level_normalize_fanout_design.md` (D88)

## Summary

The core of D88 has landed correctly and follows the D84 pattern faithfully.
The extract barrier now materializes the complete claim normalize set inside
the same transaction as its handoff (`src/rememberstack/spine/work_ledger.py:293-306`),
`complete_claim_normalize` takes a dedicated representation-scoped advisory
lock before completing the row and running the anti-join
(`src/rememberstack/spine/work_ledger.py:310-370`, `1116-1124`), the anti-join
counts `status = 'succeeded'` only and never "any terminal"
(`src/rememberstack/spine/work_ledger.py:1146-1161`), observations are buffered
to a staging table and flushed post-barrier in `(asserted_at, claim_id)` order
(`src/rememberstack/spine/fact_catalog.py:629-639`,
`src/rememberstack/workers/e3.py:704-745`), and the supersession selector is
rebuilt from origin claims rather than a worker-local id list
(`src/rememberstack/spine/fact_catalog.py:650-660`). D86 is preserved inside
the claim job unchanged: generate-only soft boundary, one type-gate retry,
identical `normalize:{claim_id}:aN` cost keys, resolver errors re-raised
(`src/rememberstack/workers/e3.py:503-586`, `193-221`). The requested test
command passes (29 passed).

Three findings block merge. Two of them are schema/readiness defects that a
unit run cannot catch, and one is a readiness dead end on a reachable ingest
path:

1. The migration breaks the D1 catalog contract in two independent ways, so
   `verify_schema` fails against a freshly migrated database.
2. `adjudicate_observations` is now a required readiness stage, but the
   zero-chunk ingest path never enqueues it, so those versions can never
   report ready on the public `pipeline_readiness` surface.
3. The derived normalize readiness row returns `now()` as `finished_at` for
   zero-claim versions, so `terminal_at` advances on every call and projection
   freshness can never be satisfied.

Four further findings are design-faithfulness or robustness gaps that should be
resolved but do not by themselves block: the §5.5 `asserted_at` direction rule
is unimplemented, a version-level row at the fan-out version still runs the
legacy serial loop rather than acting as a coordinator, `OBS_FLUSH_VERSION` is
duplicated as a string literal in the spine, and the acceptance test matrix in
§11 is essentially uncovered.

---

## Blocking

### B1 — The migration breaks the D1 catalog contract (two failures)

`src/rememberstack/spine/catalog_contract.py:144` adds
`normalize_observation_staging` to `EXPECTED_TABLES`, but neither of the two
invariants that `EXPECTED_TABLES` feeds was updated.

**Failure 1 — constraint counts.** `verify_schema` counts `pg_constraint` rows
by `contype` across exactly `EXPECTED_TABLES` and compares to a frozen dict
(`src/rememberstack/spine/catalog_contract.py:505-518`):

```python
EXPECTED_CONSTRAINT_COUNTS: Final = {"c": 53, "f": 128, "p": 66, "u": 34, "x": 1}
```
(`src/rememberstack/spine/catalog_contract.py:330`)

The new table declares a five-column `PRIMARY KEY`
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:31-33`),
which adds one `contype = 'p'` row to the counted set. Observed becomes
`"p": 67`; expected is still `66`.

**Failure 2 — table comments.** `verify_schema` requires every table in
`EXPECTED_TABLES` to carry a `COMMENT ON TABLE`
(`src/rememberstack/spine/catalog_contract.py:559-572`). The migration issues
no `COMMENT ON TABLE normalize_observation_staging`
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:19-36`),
so `commented_tables` is `len(EXPECTED_TABLES) - 1`.

**Failure scenario.** Run `uv run pytest src/tests/spine/test_migrations.py::test_postgresql_fresh_downgrade_reupgrade_mutation_and_noop_lifecycle`
against Postgres. `verify_schema` (`src/rememberstack/spine/catalog_contract.py:452`)
raises `SchemaContractError` with two problems:
`constraint counts: expected {... 'p': 66 ...}, observed {... 'p': 67 ...}` and
`table comments: expected N, observed N-1`. The same failure surfaces on any
operator-run schema verification against a head database. The requested unit
command does not exercise this — `test_migrations.py` is not in
`.github/ci/unit-paths.txt`.

**Fix.** Bump `"p"` to `67` and add a `COMMENT ON TABLE` (and, to match house
style on every other spine table, column comments) in `p9_08_0029`.

Related nit in the same edit: the entry is inserted between `mentions` and
`merge_events`, breaking the tuple's alphabetical ordering
(`src/rememberstack/spine/catalog_contract.py:143-146`). `_compare` uses sets
(`src/rememberstack/spine/catalog_contract.py:805-815`) so this is cosmetic,
but it should sort after `merge_events`. The two new indexes need no
`EXPECTED_INDEXES` entry — the index query filters by `indexname = ANY(:names)`
(`src/rememberstack/spine/catalog_contract.py:398-405`), so extras are
invisible. That is correct as written.

### B2 — Zero-chunk versions can never report ready: `adjudicate_observations` is never enqueued

`adjudicate_observations` is now an expected readiness stage
(`src/rememberstack/profiles/selfhost.py:899`), and readiness requires *every*
expected stage to be `succeeded`/`skipped` with a non-null `finished_at`
(`src/rememberstack/spine/readiness.py:155-183`). There are exactly three
producers of that stage — the fan-out's empty-claim branch
(`src/rememberstack/spine/work_ledger.py:654`), the fan-out's
already-all-succeeded replay branch (`:707`), and the barrier
(`:360`). All three sit behind the claim path.

The zero-chunk path never reaches any of them. When a representation yields no
chunks, `EmbedChunksHandler` short-circuits to `_extract_follow_up(chunks=())`
(`src/rememberstack/workers/e1.py:181-182`), which enqueues a **version-level**
`normalize_relations` row at the current `E3_NORMALIZER_VERSION`
(`src/rememberstack/workers/e1.py:630-644`). `ExtractClaimsHandler` has the
same edge (`src/rememberstack/workers/e2.py:259`, `1063-1081`).
`NormalizeRelationsHandler.handle` routes any `DOCUMENT_VERSION` target to the
serial path regardless of component version
(`src/rememberstack/workers/e3.py:135-138`), and with no claims that path goes
straight to `_terminal_branches` — `adjudicate_supersession` + `embed_claim`,
no observation flush (`src/rememberstack/workers/e3.py:260-265`, `309-345`).

**Failure scenario.** Ingest a document version whose representation produces
zero chunks (empty document, image-only PDF, whitespace-only markdown). The
pipeline runs to completion and every other stage succeeds, but
`pipeline_readiness` (MCP `pipeline_readiness` tool,
`src/rememberstack/surfaces/mcp.py:81-88`; HTTP
`src/rememberstack/surfaces/http_api.py:588`) reports
`adjudicate_observations` as `missing` forever, so `version.ready` is `false`
permanently. An agent waiting on readiness never proceeds.

**Fix.** Either enqueue the obs-flush row on the zero-chunk branch too, or make
the derived readiness treat a version with no claim rows as satisfying the
flush stage. The first is cleaner and keeps the stage chain uniform.

### B3 — Derived normalize readiness returns `now()`, so projections can never be fresh

`_NORMALIZE_CLAIM_STATUS` reports `COALESCE(max(p.finished_at), now())`
(`src/rememberstack/spine/readiness.py:299`) and reports `succeeded` when a
version has zero claims (`:288`). That row is then written into `by_key`
**unconditionally** (`src/rememberstack/spine/readiness.py:144-150`), unlike
the extract merge directly above it, which only overwrites a missing or
non-succeeded entry (`:129-141`). `finished_at` feeds `terminal_at`
(`:169-172`), and projection freshness is `built_at >= terminal_at` (`:195-201`).

**Failure scenario.** A version has chunks but extraction yields zero claims (a
boilerplate or navigation page — routine at corpus scale). Normalize resolves
to `succeeded` with `finished_at = now()` on *every* `inspect()` call. P2/P3
snapshots were built in the past, so `built_at >= terminal_at` is false every
time, and `inspect(require_projections=True)` never returns ready no matter how
recently the projections ran. The same instability applies to the zero-chunk
path in B2, where the unconditional overwrite also discards the real
`finished_at` from the genuine version-level row that `_VERSION_WORK` loaded.

The adjacent extract query avoids this by falling back to the version's
succeeded `embed_chunk` timestamp before reaching for `now()`
(`src/rememberstack/spine/readiness.py:252-256`, `269-274`). The normalize
query has no such fallback.

**Fix.** Give the zero-claim case a stable timestamp (the version's succeeded
extract or embed row), and mirror the extract merge's precedence at
`:144-150` instead of overwriting unconditionally.

---

## Non-blocking

### N1 — §5.5's `asserted_at` direction rule is not implemented

§5.5 binds: "evidence used in prompts / boundary direction prefers **claim
`asserted_at`**, not ingestion/`occurred_at` order, when choosing predecessor
vs successor among supporting claims (impl may need a small supersession helper
change; bind the product rule here)", and §14 lists the supersession selector
as *not open*. `src/rememberstack/spine/supersession.py` is untouched by this
PR and still picks each relation's representative supporting claim by ingestion
order (`src/rememberstack/spine/supersession.py:389-393` and `412-416`, both
`ORDER BY c.ingested_at DESC LIMIT 1`).

The selector half of §5.5 is correct — `relation_ids_for_origin_claims` binds
on origin claims and normalizer version
(`src/rememberstack/spine/fact_catalog.py:650-660`) — and the closure boundary
itself does use `asserted_at` (`src/rememberstack/spine/supersession.py:253-258`).
But *which* claim's `asserted_at` becomes the boundary is still chosen by
ingestion time.

**Failure scenario.** Backfill an older document (asserted 2019) after a newer
one (asserted 2024) about the same subject/predicate block. The 2019 claim has
the later `ingested_at`, so it is selected as the relation's representative
evidence, and `_CLOSE_WINDOW` stamps `valid_until` with the 2019 assertion
time — exactly the "late older testimony wins solely by finishing later"
outcome §5.5 forbids.

Separately, the new-relation adjudication order is now `ORDER BY e.relation_id`
(`src/rememberstack/spine/fact_catalog.py:658`) — a random v4 UUID sort. The
pre-D88 serial path fed relations in claim order
(`claims_for_chunks ORDER BY ingested_at, claim_id`), so the ladder's
order-sensitive window closures now traverse in an arbitrary sequence. Ordering
the selector by claim `asserted_at` would fix both halves in one change.

### N2 — A version-level row at the fan-out version runs the serial loop, not a coordinator

§5.3 binds: "Legacy/new **coordinator** at fanout version (if any version-level
row exists): fan-out only + barrier check; **never** mark version normalize
'complete' by coordinator success alone." `handle` branches on `target_kind`
alone (`src/rememberstack/workers/e3.py:135-138`), so a `DOCUMENT_VERSION` row
at the *current* `E3_NORMALIZER_VERSION` executes `_handle_version_serial`
(`:246-307`) — the whole-version serial loop with in-claim D43 adjudication
(`:295-302`) and a worker-local `relation_ids` handoff (`:305`), i.e. all three
behaviors D88 exists to remove.

Today the only producers of such a row are the zero-chunk paths in B2, where
the claim set is empty and the damage is limited to the missing flush stage.
But the guard the design asks for is absent, so any replay or operator enqueue
of a version-level normalize row at the fan-out version silently reverts to
pre-D88 semantics. Gate the serial branch on
`work.component_version != E3_NORMALIZER_VERSION`.

### N3 — `OBS_FLUSH_VERSION` is duplicated as a string literal in the spine

`src/rememberstack/spine/work_ledger.py:632-633` hardcodes
`"e3-obs-flush-2026.08a:claim-fanout-1"` with the comment "Keep string literal
aligned with workers.e3.OBS_FLUSH_VERSION (avoid import cycle)". Avoiding the
cycle is right, but nothing enforces the alignment: the new test only asserts
`OBS_FLUSH_VERSION.startswith("e3-obs-flush")`
(`src/tests/workers/test_e3_claim_normalize_fanout.py:22`), which passes for
any bumped value.

**Failure scenario.** Bump `OBS_FLUSH_VERSION` in
`src/rememberstack/workers/e3.py:64` without editing the spine literal. The
barrier path enqueues at the new version (it reads
`barrier.obs_flush_component_version`, `src/rememberstack/spine/work_ledger.py:356`)
while the empty-claim path enqueues at the stale one
(`:654`). The stale row never matches `_expected_components`
(`src/rememberstack/profiles/selfhost.py:899`), so zero-claim versions become
permanently not-ready — the same shape as B2, silently, on a routine version
bump. Add a test asserting the two strings are equal, or move the constant to a
module both can import.

### N4 — Per-claim validation loads the representation's entire chunk set

`_handle_claim` validates claim→representation membership by materializing
every chunk row of the representation and building a set
(`src/rememberstack/workers/e3.py:161-168`), once per claim job.

At the BEAM scale this design targets (§8 asks for an EXPLAIN on the
expected-set query), a representation with ~5k chunks and ~15k claims means
15k full-chunk-row scans — ~75M rows read, plus the `ChunkForEmbedding`
model-validate cost, purely to check one membership bit. A direct
`claim → chunk → representation` lookup (or returning `chunk.representation_id`
from `claim_for_normalization`) is O(1) per job and validates the same
invariant more precisely.

The equivalent load in `AdjudicateObservationsHandler`
(`src/rememberstack/workers/e3.py:747-752`) runs once per version and is fine.

### N5 — The §11 acceptance matrix is essentially uncovered

The design lists fifteen acceptance tests; the branch adds two assertions in a
fully-faked unit test (`src/tests/workers/test_e3_claim_normalize_fanout.py`).
None of the barrier, fan-out, readiness, or ordering behavior is exercised. In
particular §5.4 binds a specific test — "two real connections interleaved so
each sees the other running before either commits; expect exactly one
downstream pair" — and calls the lock "**mandatory**, not a nit". That test
does not exist; nothing references `complete_claim_normalize` outside
`src/rememberstack/spine/work_ledger.py`.

The lock itself reads correctly, so this is a coverage gap rather than a known
defect. But B1, B2, and B3 are all cases a Postgres-backed test of the shape
§11 describes would have caught. At minimum: the last-claim race, one claim
dead-lettered ⇒ no downstream and readiness false, the zero-claim version, and
reverse-completion-order observations.

### N6 — Claim payload validation is not deployment-scoped

§5.1 binds membership as "**deployment-scoped** … so payload UUIDs cannot cross
tenants or versions", §7 requires rejecting cross-deployment ids, and §11 lists
"Cross-tenant payload lie ⇒ Rejected". `_handle_claim` checks `doc_id` equality
and chunk membership (`src/rememberstack/workers/e3.py:156-168`), but
`claim_for_normalization` filters on `claim_id` alone
(`src/rememberstack/spine/claim_catalog.py:333-339`) and `chunks_for_embedding`
on `representation_id` alone (`src/rememberstack/spine/chunk_catalog.py:80-95`),
so a work row carrying another deployment's coordinates passes. The barrier
queries have the same gap (`src/rememberstack/spine/work_ledger.py:1126-1145`).

This matches existing house style — D84's `_BARRIER_EXPECTED_CHUNKS`
(`src/rememberstack/spine/work_ledger.py:1164-1171`) is also representation-only
— and UUID uniqueness makes it hard to hit accidentally, so it is not a
regression. But D88 explicitly binds the check, so either implement it or
record the deviation.

---

## Verified correct

Worth stating so a re-review does not re-litigate these:

- **Atomic fan-out (§5.2).** The complete claim set is inserted inside the
  extract barrier's transaction, before the barrier commits
  (`src/rememberstack/spine/work_ledger.py:287-306`, `618-722`). Children
  cannot exist without the handoff, or vice versa. The
  already-all-succeeded replay check runs in the same transaction edge
  (`:696-721`) as §5.2 requires.
- **Barrier lock (§5.4).** A distinct `d88-normalize-barrier:` key
  (`src/rememberstack/spine/work_ledger.py:1116-1124`) is taken before
  `_COMPLETE` and before the anti-join
  (`:326-337`), one bigint via `hashtextextended`. Two last claims serialize:
  the second waits on the lock until the first commits, then sees the first's
  `succeeded` row. Exactly one downstream enqueue, and the enqueue is
  `ON CONFLICT DO NOTHING` idempotent anyway
  (`src/rememberstack/spine/work_ledger.py:886-887`).
- **Barrier admits only `succeeded` (§3).** `_BARRIER_READY_CLAIMS` joins
  `p.status = 'succeeded'` (`src/rememberstack/spine/work_ledger.py:1157`), so
  `dead_letter`/`failed`/missing block. Readiness agrees
  (`src/rememberstack/spine/readiness.py:293`).
- **Soft drops still count.** `_handle_claim` ignores `_normalize_claim`'s
  soft-skip return and returns the barrier regardless
  (`src/rememberstack/workers/e3.py:193-244`), so a D86 content-poison claim
  yields `succeeded` and does not wedge the version — §3 and §5.8.
- **Ordered observation flush (§5.6).** Claim jobs stage only
  (`src/rememberstack/workers/e3.py:222-231`); the flush loads
  `ORDER BY c.asserted_at NULLS LAST, s.claim_id, …`
  (`src/rememberstack/spine/fact_catalog.py:637`) and applies D43 per entity
  under the entity lock. Staging is claim-idempotent via its PK
  (`_UPSERT_OBS_STAGING … DO NOTHING`,
  `src/rememberstack/spine/fact_catalog.py:614-625`) and
  `add_observations` is evidence-PK idempotent
  (`src/rememberstack/spine/observation_adjudication.py:130-136`), so a crash
  between flush and `clear_staged_observations`
  (`src/rememberstack/workers/e3.py:741-745`) replays safely.
- **Continuous ingest (§4).** Every barrier key is
  `(representation_id, chunker_version, normalize_version)`, so concurrent
  versions and lineages hold independent barriers. `_CLAIM_SELECT` does not
  filter `component_version` (`src/rememberstack/spine/work_ledger.py:918-932`),
  so legacy pre-fan-out version rows stay claimable and drain — §5.7 cutover.
- **D86 preserved on the claim path (§5.8).** Prompt, `temperature=0.0`, the
  two-attempt type gate, generate-only soft boundary, `ProviderCallError`
  re-raise, `UnregisteredEntityTypeError` and entity-type FK logging, and the
  `normalize:{claim_id}:aN` / `:aN:failure` cost keys are all unchanged
  (`src/rememberstack/workers/e3.py:193-221`, `503-586`). The shared
  `_normalize_claim` gained only the `staged_observations` sink
  (`:347-371`, `495-500`), so both paths run identical gates.
- **Lane routing.** `adjudicate_observations` is not in `UNLANED_STAGES`
  (`src/rememberstack/spine/catalog_contract.py:274-285`), so it correctly
  requires steady/backfill, and both enqueue sites propagate the inbound lane
  (`src/rememberstack/spine/work_ledger.py:353`, `652`).
- **Migration chain.** `p9_08_0029` has a single parent (`p9_07_0028`) and no
  sibling claims that revision; downgrade drops all three objects.
- **No import cycle.** `work_ledger` imports `ClaimNormalizeBarrier` lazily
  inside the method (`src/rememberstack/spine/work_ledger.py:323-324`),
  matching the `ExtractChunkBarrier` precedent.
- **Wiring.** `AdjudicateObservationsHandler` is exported
  (`src/rememberstack/workers/__init__.py`), composed
  (`src/rememberstack/profiles/selfhost.py:740-751`), given a compose service
  (`compose.yaml`), and added to the CI unit paths. The website docs do not
  enumerate worker services or pipeline stages, so no same-PR docs obligation
  is triggered by the compose change.

## Test run

```
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
              src/tests/workers/test_e3_unknown_entity_type_gate.py \
              src/tests/profiles/test_selfhost_profile.py -q
29 passed in 4.27s
```

B1 is not reachable from this command — it fails in the Postgres-backed
`src/tests/spine/test_migrations.py`, which is not in
`.github/ci/unit-paths.txt`.

## What would flip this to APPROVE

Fix B1 (bump `"p"` to 67, add the table comment), B2 (enqueue the flush stage —
or exempt it in readiness — on the zero-chunk path), and B3 (stable
`finished_at`, and mirror the extract merge precedence). N1 and N2 should land
with them since both are explicitly bound by the design; N3 needs only a
one-line equality test. N5 is the honest gap — at least the §5.4 two-connection
race test and the dead-letter-blocks-readiness test belong in this PR, because
they are what the design calls mandatory.
