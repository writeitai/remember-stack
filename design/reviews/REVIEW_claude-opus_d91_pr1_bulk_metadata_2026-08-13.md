# Implementation review — D91 PR1 bulk fact-metadata merge

**Agent:** claude-opus
**Date:** 2026-08-13
**Target:** branch `feat/d91-pr1-bulk-metadata`, PR #271
**Commit reviewed:** `73bcadbd`
**Scope:** `src/rememberstack/adapters/selfhost/lance.py` (`update_fact_metadata`
and its new helpers) and `src/tests/adapters/test_lance_retrieval.py` (new
metadata tests)
**Design:** D91 `plan/designs/p1_lance_maintenance_design.md` §5.2 / §15 PR1 —
read at `87079b4b` on `design/d91-p1-lance-maintenance` (design PR #270, still
open; see process note P1 below)

---

## Verdict

**APPROVE_WITH_NITS.**

The PR does what D91 PR1 requires: the per-row `table.update` loop (forbidden
by §5.2.1 once the design lands) is gone, replaced by ensure-join-keys →
dedupe → skip-unchanged → batched matched-only `merge_insert`, with no
`optimize()` anywhere on this write path. All five focus areas check out, and
I verified the PR's validation claims by execution rather than by reading the
PR body. The nits below are robustness and design-conformance items; none of
them is reachable from a live flow today, so none blocks landing.

## What I verified by execution

- `uv run pytest src/tests/adapters/test_lance_retrieval.py` — **7 passed**
  (matches the PR body).
- `ruff check` + `ruff format --check` on both touched files — clean.
- `pyright` on both touched files — 0 errors, 0 warnings.
- A probe script against the shipped adapter confirming nit N1 below
  (cross-kind `fact_id` truncation in the skip-unchanged lookup) — the defect
  is real, and its only trigger is a cross-kind UUID collision that no live
  flow can produce.
- `uv.lock` resolves `lancedb == 0.34.0`, the exact version §5.2.1 pins its
  column-preservation guarantee to (see N6).

## Focus-area assessment

**1. Matched-only merge, no vector wipe — correct.**
`_merge_insert_matched` (`lance.py:405`) uses `when_matched_update_all()` with
no insert clause, exactly as §5.2.1 requires ("DO NOT
`when_not_matched_insert_all` — unmatched would insert null vectors"). The
payload (`fact_metadata_merge_payload`, `lance.py:83`) carries only the three
join keys plus the five mutable eligibility scalars, so `label` and `vector`
are never in the source schema and survive the merge. The test
`test_fact_metadata_merge_preserves_vector_and_label` proves both halves
against real Lance: the present row keeps its exact vector and label while
`status` flips, and the missing-id row does **not** appear
(`table.count_rows() == 1`), so unknown ids are not inserted as null-vector
skeletons. The merge key `["deployment_id", "kind", "fact_id"]` matches
`upsert_facts` (`lance.py:296`) as §5.2.1 binds.

**2. Skip-unchanged — correct, with a strong test.**
`fact_metadata_scalars_differ` (`lance.py:111`) compares exactly the five
mutable columns (`_FACT_METADATA_VALUE_COLUMNS`, `lance.py:64`) that §5.2.2
enumerates, and treats a missing Lance row as "no merge" — consistent with
matched-only semantics. Types line up on both sides (sentinel-encoded `int64`
epochs and plain strings; no `None` ever reaches the comparison because absent
times are encoded as `_MIN_TIME_US`/`_MAX_TIME_US`).
`test_fact_metadata_skip_unchanged_does_not_grow_versions` asserts the Lance
**table version is unchanged** after an identical second refresh — the
strongest available signal that a fully-skipped call opens no merge commit,
which is what §5.2.2 point 2 demands ("If every candidate is skipped, do not
open a merge at all"). This matters because §5.2.1's delete-and-reinsert note
means every needless merge pushes rows back into the unindexed tail.

**3. Join-key ensure before merge — correct, including the kind-index guard.**
`_ensure_facts_join_indexes` (`lance.py:368`) runs before any lookup or merge,
per §15 PR1 ("must not land large merges without join-key indexes"): BTree on
`deployment_id` and `fact_id` (the latter newly mandatory for this path, per
§5.2.1), and Bitmap on `kind` only when no index covers it yet
(`_column_has_index`, `lance.py:375`). That guard is necessary, not cosmetic:
`upsert_facts` already creates a **BTree** on `kind` (`lance.py:326`), and
`_create_index_with_retry` matches on `(index_type, column)`, so an unguarded
`_ensure_bitmap_index` would create a second, differently-typed index on the
same column of an already-written store. The Bitmap branch therefore fires
only on upgraded stores that predate the kind index. Full kind-BITMAP
consistency is explicitly §15 PR2 scope. The preservation test asserts the
`fact_id` and `deployment_id` BTrees exist after the call.

**4. No write-path optimize — correct.**
`update_fact_metadata` no longer calls `_maintain_indexed_tail` (the old code
did, on every call). Nothing in the new path reaches `optimize()`. This is
the interim state D91's rollout section explicitly blesses ("Between PR1 and
maintain worker: **no** synchronous write-path optimize; tails grow only for
rows that actually change eligibility"). The upsert paths keep their existing
write-path maintenance, which is the right interim call — stripping them
before the maintain worker exists (PR3) would leave no maintenance anywhere.

**5. Tests drive the shipped adapter — yes.**
Both new tests construct `LanceChunkIndex` and call the shipped
`upsert_facts` / `update_fact_metadata`; raw `lancedb.connect` is used only to
*inspect* the resulting table (row contents, version, indices), never to
reimplement the write path. The duplicate `present_id` row in the first
test's input also exercises `dedupe_fact_metadata_rows` for real — without the
dedupe, Lance rejects the ambiguous merge for the whole batch (§5.2.1), so the
test would fail. Last-write-wins tie-break matches the design's binding
choice.

## Nits (ordered by importance)

### N1 — Skip-unchanged lookup is under-keyed and can silently drop an update on cross-kind id reuse

`_fact_metadata_by_key` (`lance.py:380`) filters on `deployment_id` and
`fact_id IN (…)` but **not** `kind` (`lance.py:396`), then truncates at
`.limit(len(items))` (`lance.py:398`). Everywhere else in this adapter the
facts key is the 3-tuple `(deployment_id, kind, fact_id)` — the merge key, the
dedupe key, the upsert key — but this one query assumes `fact_id` alone is
discriminating. If a `fact_id` exists under **both** kinds, rows of the
un-requested kind can consume the limit and evict a requested row from the
result; the evicted row then reads as "missing from Lance" and its update is
**silently skipped** — stale eligibility metadata with no error and no signal.

I confirmed this by execution against the shipped adapter: with rows
`(relation, A)`, `(observation, A)`, `(relation, B)` in storage order, the
batch `[(relation, A → invalidated), (relation, B → invalidated)]` leaves
`(relation, B)` **active**.

Why this is a nit and not a blocker: relation and observation ids are
independently generated UUIDs from separate Postgres tables, so a cross-kind
collision is cryptographically negligible — no live flow can reach this today,
even though `fact_metadata_for_document` batches routinely mix kinds. But the
adapter's own key contract permits it, and a future migration or synthetic-id
scheme would hit it silently. Fix is small: group the lookup by
`(deployment_id, kind)` and add `AND kind = '…'` to the predicate, which also
makes the query fully keyed against the ensured indexes.

### N2 — Lookup reads full rows (vectors included) where the design binds a projection

§5.2.2 specifies the skip-unchanged read as a "**bounded projection of join
keys + mutable columns**". The implementation's
`table.search().where(…).limit(…).to_list()` has no `.select(…)`, so every
candidate row is materialized with its `vector` (and `label`) just to compare
five scalars. At the BEAM scale this PR exists for (~8k facts per document),
that is thousands of needless vector decodes per refresh on the exact path
being optimized. Add
`.select(["deployment_id", "kind", "fact_id", *_FACT_METADATA_VALUE_COLUMNS])`.

### N3 — `metadata_merge_batch_size` ships as a constant, not the settings knob §15 PR1 names

The §15 PR1 row lists `metadata_merge_batch_size`, and §5.4 defines it as a
settings knob (default 500). The PR ships `METADATA_MERGE_BATCH_SIZE: Final =
500` (`lance.py:61`) — deliberately public-named, but not wired to any
settings surface, and the PR body's limitations section does not mention the
deferral. Deferring is defensible: the design's rollout note ties settings
surfaces to compose-env and docs-site updates in the same PR (D66), which
would bloat PR1 considerably. But then the deferral should be *stated* in the
PR body (or the follow-up PR named), so the gap is a recorded decision rather
than an accidental drop.

### N4 — `_update_with_retry` is now dead code

The only caller was the old per-row loop; nothing references
`_update_with_retry` (`lance.py:1194`) anymore. §5.2.3 is explicit: "Do not
introduce per-row `update` anywhere else in the adapter." Leaving the helper
in place is an invitation to do exactly that. Delete it.

### N5 — `_merge_insert_matched` discards `MergeResult`, so `metadata_miss` has no anchor

§5.2.1 derives the `metadata_miss` metric from
`batch_len - MergeResult.num_updated_rows` and explicitly forbids computing it
via a second id-lookup pass. The implementation ignores `execute()`'s return
value entirely. Counters are deferred by the PR body ("change-mass counters …
later sequential PRs"), which is fine — but when that PR arrives, the count
must come from this return value, and nothing in the code marks that. A
one-line comment (or returning the result from `_merge_insert_matched`) would
keep the follow-up from re-deriving misses the forbidden way.

### N6 — Version-sensitivity of the preservation guarantee (observation, no action)

§5.2.1's core claim — a partial matched payload "updates only the supplied
columns and **preserves `label` and `vector`**" — is stated for pinned
`lancedb==0.34.0`. `uv.lock` resolves exactly 0.34.0 today, but
`pyproject.toml` declares the floor `lancedb>=0.34.0`, so a future re-lock can
move the resolved version under the same declaration. The shipped
preservation test is the guard that would catch a semantic change, which is
the right defense; noting it here so the test is never weakened to a
mock-based one.

## Process note

### P1 — Cited design is not yet on `main`

The PR body cites `plan/designs/p1_lance_maintenance_design.md` §5.2 / §15,
which exists only on `design/d91-p1-lance-maintenance` (design PR #270, open,
at r-nits-absorbed `87079b4b`). The implementation conforms to that text as
reviewed. Land #270 before (or with) #271 so the sections this PR — and this
review — cite exist on `main`.

## Summary

| Focus area | Result |
| --- | --- |
| Matched-only merge, vectors/labels preserved, no null-vector inserts | **Pass** (test + merge-clause inspection) |
| Skip-unchanged, no merge commit when nothing changed | **Pass** (table-version assertion) |
| Join-key ensure before merge, kind-index guard correct | **Pass** |
| No write-path `optimize()` | **Pass** (interim state per D91 rollout §3) |
| Tests drive shipped adapter, not a reimplementation | **Pass** |

**APPROVE_WITH_NITS** — N1 (under-keyed lookup) and N2 (missing projection)
are the two worth fixing before the next D91 PR builds on this path; N3–N5 are
hygiene; N6/P1 are records, not asks.
