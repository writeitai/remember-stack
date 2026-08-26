# Handoff — D95–D97 entity identity and retrieval (2026-08-26)

**Audience:** the next agent or human, with no memory of the Grok session that
cut this work. Read this file first, then the binding docs it names. Do not
treat analysis, proposals, or this handoff as architecture.

**Repos**

| Repo | Path | Role |
|---|---|---|
| RememberStack (implementation) | `/Users/jpuc/code/moje/remember-stack` | All D95–D97 design and code |
| UMC (cloud product) | `/Users/jpuc/code/moje/ultimate_memory_cloud_2/ultimate-memory-cloud` | Not the implementation surface for this program |

The Grok session that produced this handoff was attached to the UMC workspace
and executed RememberStack from the sibling path above.

**Operator constraint from that session:** dual-review every implementation PR
(Codex `gpt-5.6-sol` xhigh + Antigravity `agy`) before merge. Squash-merge to
`main`. CLA checkbox in the PR body. No LoCoMo optimization. No backward
compatibility. Do not reintroduce types, hats, or exact-T0 auto-merge.

---

## Binding vs not-binding

| Kind | Path | Use |
|---|---|---|
| Binding design | `plan/designs/entity_identity_and_retrieval_design.md` | What to build |
| Binding decisions | `decisions.md` D95, D96, D97 | Canonical numbered log |
| Sequencing (not design) | `plan/plans/entity_identity_and_retrieval.md` | WP-I.1 … I.7 order |
| Analysis (not binding) | `plan/analysis/entity_identity_and_retrieval_analysis.md` | Why; never cite as architecture |
| Unchosen proposal | `design/proposals/optional-exact-t0-accept.md` | Exact-T0 flag, **default off**, **not WP-I.5** |
| Working agreement | `CLAUDE.md` / `AGENTS.md` | Rule 2: full scope, not MVP |

**D95.** Identity is the real-world referent, not the spelling. T0 lists
candidates; it never auto-merges. Repeats of a known person are cheap via T3
(profile embedding), not via resurrecting exact-lemma merge.

**D96.** No entity types as identity. No `entities.type`. Profile is
observation prose. `predicate_signatures` / D18 domain-range as a write gate
are gone. Unknown predicates still D5 (`other:`).

**D97.** Default retrieval is `resolve` → observations+relations lookup →
`neighborhood` with **empty predicates** → ID-constrained fact-text search.
No type filter. No new query-path LLM.

**Rejected:** “enable exact-T0 after a large corpus.” Birthday-paradox / more
entities makes collision *worse*. The optional flag is unchosen and **closed
on unique-namespace trigger**. Do not ship `t0_exact_accept` in WP-I.5.

---

## What is already on `main`

`origin/main` at handoff: `dc6eae4b` (`feat(e3): WP-I.1 bare-noun refusal and source/canonical aliases (#308)`).

| PR | What | Merged |
|---|---|---|
| [#304](https://github.com/writeitai/remember-stack/pull/304) | D95–D97 design | 2026-08-26T14:11:06Z |
| [#307](https://github.com/writeitai/remember-stack/pull/307) | T0-never-merge written; exact-T0 stays a proposal | 2026-08-26T14:43:32Z squash `ed7bff50` |
| [#308](https://github.com/writeitai/remember-stack/pull/308) | **WP-I.1** bare-noun + source/canonical aliases | 2026-08-26T19:11:43Z squash `dc6eae4b` |

**WP-I.1 shipped behavior**

- Bare head-noun refusal: `src/rememberstack/spine/entity_eligibility.py`
  (`game` not minted; `FIFA 23` may mint). `application` is **not** in the
  bare-noun list (would drop App/Application).
- `EntityRef`: `name` + optional `surface`. Legacy JSON `type` discarded
  (`model_validator`).
- E3 prompt 08b then 08c: source surface + canonical.
- Resolver alias upsert: `llm_canonical` plus claim-grounded `source` only
  when `surface_appears_in_claim`. Ungrounded canonical is never written as
  `source`.
- Guard **writer** exists (`generic_identifier_guard`) for later I.5
  T1/T2 downweight — not T0 auto-merge.
- Dual reviews r3 **Approve**:
  `design/reviews/REVIEW_codex-sol_wp_i1_extract_aliases_r3_2026-08-26.md`,
  `design/reviews/REVIEW_agy_wp_i1_extract_aliases_r3_2026-08-26.md`.

I.1 does **not** change T0 verdicts. Exact lemma still auto-merges in
production `CascadeResolver.resolve` until WP-I.5.

---

## Open work: WP-I.2 PR #311

**PR:** https://github.com/writeitai/remember-stack/pull/311
**Branch:** `feat/wp-i2-type-cut` → `main`
**HEAD at this handoff:** `2b87db44` (`fix(er): restore non-entity INSERTs and D96 chain assertions`)
**Working tree:** clean and pushed (`origin/feat/wp-i2-type-cut`).

### Commits on the PR (oldest first)

1. `a802ebb3` `feat(er): D96 hard type cut on ingest, P2/P3, and resolve`
2. `cc485c5d` `fix(er): replace entities_current before dropping type column`
3. `b4f2a382` `fix(er): finish D96 type-cut consumers and view dependents`
4. `4b7efcdb` `docs(review): file WP-I.2 r1 dual reviews of the incomplete cut`
5. `2b87db44` `fix(er): restore non-entity INSERTs and D96 chain assertions` ← **HEAD**

Squash-merge when dual-review **Approve** on this HEAD and CI is green. Do not
merge r1 Request-changes of the incomplete cut as if they still apply.

### What I.2 actually changed (as-built)

**Migration** `src/rememberstack/spine/migrations/versions/p9_14_0035_drop_entity_type.py`
(head after I.1 is `p9_13_0034`):

- `CREATE OR REPLACE` `memory_v1.entities_current` with `NULL::text` /
  `NULL::real` for vacated `entity_type` / `type_confidence` **column names**.
  Public column names stay so query-space dependents do not CASCADE-drop.
  **Do not filter** those vacated columns.
- Same for private `v_memory_mention_current_content` (`emitted_type` /
  `type_confidence` vacated NULL).
- `v_graph_entities.type` vacated `NULL::text` (column kept).
- **Then** `DROP CONSTRAINT` / `DROP INDEX ix_entities_type` /
  `DROP COLUMN` `entities.type`, `entities.type_confidence`,
  `mentions.emitted_type`, `mentions.type_confidence`.
- Drop table `predicate_signatures`.
- Downgrade restores nullable type columns, the mention helper, graph
  entities, and `entities_current` **with the provenance EXISTS subquery**.

**Writers / runtime**

- `EntityRef` name+surface only. `ResolvedEntity` / `ResolutionCandidate` /
  `GraphNode` / `EntityCandidate` have no type.
- E3 `e3-normalize-2026.08c:temp0-1:claim-fanout-1:bare-noun-1:no-types-1`.
  No D86 retry, no `_signature_allows`, no `predicate_signatures` read.
  Unknown predicates still dropped unless `other:` (D5).
- Bootstrap still seeds `entity_types` + `predicates` (unused type seed).
  `predicate_signatures_count=0`. Extension packs no longer INSERT signatures.
- P2 Entity nodes untyped (`p2-rebuild-2026.08`). P3 path `entities/<id>/`
  (`p3-corpusfs-2026.08`). Relative doc links from an entity page are
  `../../documents/` (was `../../../documents/` under `entities/<type>/<id>/`).
- Query `resolve` has no `entity_type` (HTTP, SDK, assured ops, LoCoMo
  dispatcher). `typed_absence` no longer filters by type.
- Hard-forget no longer `SET type_confidence`.
- Query sandbox entity filters: empty allowlist (vacated NULL must not be
  used as a filter). Confirmation SQL may still *project* `entity_type` as
  always-NULL.

**Catalog / manifest pins (HEAD)**

- Alembic head pin: `p9_14_0035`. Table count **72**. `EMPTY_AT_HEAD` without
  `predicate_signatures`.
- `EXPECTED_CONSTRAINT_COUNTS`: `{c: 68, f: 127, n: 548, p: 72, u: 36, x: 1}`
  (pinned from delta; **workers integration on CI passed** this pin).
- Surface manifest hash:
  `58a6e5646c8d1fe96ec4e30b87031fc8a100e2f7e59749bc396aa3c8cdf192a3`
- LoCoMo protocol fingerprint pin in
  `src/tests/benchmarks/test_locomo_runner.py`:
  `9e7609222975165ada2f4871ff7c5af92d76abb0de288bb829a296b583592844`
- LoCoMo ingest `normalize_relations` attested as the 08c no-types component.

### Dual review status (I.2)

| Round | Codex | agy | Against |
|---|---|---|---|
| r1 | Request changes | Request changes | incomplete cut (`cc485c5d`) |
| r2 | **in flight / stale** (started on `4b7efcdb`, Grok session ran out of quota before it finished writing the file) | **Approve** | `4b7efcdb` |
| r3 needed | yes, on **HEAD `2b87db44`** | optional delta vs r2 (small INSERT/comment/test fixes) | `2b87db44` |

Filed under `design/reviews/`:

- `REVIEW_codex-sol_wp_i2_type_cut_r1_2026-08-26.md`
- `REVIEW_agy_wp_i2_type_cut_r1_2026-08-26.md`
- `REVIEW_agy_wp_i2_type_cut_r2_2026-08-26.md`

**Do not merge on r1.** Re-run both reviewers against `2b87db44` (or whatever
HEAD is after further CI fixes). Copy new files into `design/reviews/` as
`*_r3_*` and commit them on the same branch before merge.

### CI at handoff (re-check; do not trust this snapshot)

Last observed run on `2b87db44`: contract smoke, quality, unit, adapters,
**workers all SUCCESS**. Surfaces was still `IN_PROGRESS`. Re-run:

```bash
cd /Users/jpuc/code/moje/remember-stack
gh pr checks 311
gh pr view 311 --json mergeable,mergeStateStatus,statusCheckRollup
```

If surfaces fails, it is likely another test INSERT with a leftover type
value or a GraphNode/`entity_type` assertion. Pattern that already bit us:

- Removing `type` from `INSERT INTO entities (... type, ...)` **without**
  removing the matching VALUES slot (`:kind`, `:type`, `'Person'`).
- A global regex that also ate `'Person'` / `'Document'` / `'Task'` from
  `golden_pairs`, `entity_types`, and `processing_state` INSERTs. Those were
  restored in `2b87db44`. Do not re-run a repo-wide type-literal strip.

### Merge recipe (I.2)

1. CI green on HEAD.
2. Dual-review Approve on HEAD (Codex + agy), files committed under
   `design/reviews/`.
3. CLA checkbox already in the PR body (`- [x] I have read and agree to the
   [RememberStack Contributor License Agreement v1.0](...)`).
4. Squash-merge to `main` (same as I.1). Update PR body “stacked on #308”
   is already true (base is `main`).

```bash
gh pr merge 311 --squash --delete-branch
```

---

## Immediately after I.2 merges: WP-I.3 and WP-I.4

May be **developed in parallel**. Both must **merge before I.5**.

### WP-I.3 — eval (`judge_pair`)

**File:** `src/rememberstack/eval/resolution.py`

**Current bug (pre-I.3, still on this branch):**

```python
if lemma_a == lemma_b:
    return True, "T0"
```

That makes same-lemma **non-matches** invisible. D95 cannot be measured.

**Do**

- Stop auto-true on lemma equality.
- Golden schema not keyed by `entity_type` (column may remain as a vacated
  stratum label; do not use it as identity).
- One **global** P/R curve **plus** per-tier diagnostics (keep deciding tier).
- Land design §8 fixtures: same-name non-match; empty-profile John.
- Suite must not crash without types.

**Do not** delete the deciding tier. Do not treat I.3 as “T0 now lists
candidates” — that is I.5, and it waits for a **recorded passing I.3+I.4
eval run**.

### WP-I.4 — profile + T3

- New profile refresher (compose onto observation-flush **or**
  `ProfileRefresherHandler`). `REFRESH_PROFILE` exists as an unused enum.
- T4 prompt includes profile + salient observations.
- T3 embeds **name+profile**, not name-only.
- Debounce on evidence change.
- **D74:** forget of document A on a **shared** entity invalidates/rebuilds
  profile (forgotten distinctive phrase gone from summary, salient inputs,
  vector, search).
- Empty profile is fail-safe (do not T3-accept on an empty profile).

Acceptance from the plan: “is a bank” / “lives in Prague” appear in T4; two
same-name vectors differ once profiles differ; shared-survivor forget test
green.

### Then WP-I.5 — T0 as candidates only

**Not done.** Production resolver still:

```python
if exact is not None:
    return self._record(..., method="T0", confidence=1.0, created=False)
```

I.5 after recorded passing I.3+I.4 eval:

- Hits = distinct active `entity_id`s.
- T3 may accept **one** candidate when profile exists (design §3.1.1).
- T4 when empty profile / conflict / several candidates.
- Same lemma may mint.
- `resolution_exclusions` on T4 no-match.
- Populate `generic_identifier_guard` when a lemma spans ≥2 **entities**
  (blocking, not a T0 verdict).
- Father/son → two ids (T4); empty-profile second `Jan` → T4 not T0 merge;
  repeat profiled `James` → T3 without T4; SAP shorthand → one id via T3/T4
  not T0.

**Do not** implement `t0_exact_accept`.

### WP-I.6 — D97 retrieval

Can start against fixtures after I.2; **ships after I.5**.

`resolve` → lookup observations+relations → `neighborhood` empty predicates
→ ID-constrained fact-text search (`assured_operations.py`,
`operation_executor.py`, `query_engine.py`). Optional dynamic predicate
(any stored name, including `other:`). No type filter. No new query-path LLM.

### WP-I.7 — docs (D66)

Same-PR website pages for each **user-visible** WP, describing shipped
behavior only (`website/src/app/docs/**`).

---

## Dual-review CLIs (copy exactly)

From `/Users/jpuc/code/moje/remember-stack`. Reviewers must **not** modify
tracked files. Write to scratch, then copy into `design/reviews/`.

Scratch used this session:
`/var/folders/wt/plp93ggs40586mdsvzzqy4c40000gp/T/grok-goal-6edec73546f9/implementer/`

```bash
# Codex
codex exec --dangerously-bypass-approvals-and-sandbox \
  --model gpt-5.6-sol \
  -c 'model_reasoning_effort="xhigh"' \
  -C /Users/jpuc/code/moje/remember-stack \
  "$(cat /path/to/prompt.txt)"

# Antigravity
agy --dangerously-skip-permissions --print-timeout 180m0s -p "$(cat /path/to/prompt.txt)"
```

Prompt must name the PR, `origin/<branch> vs origin/main`, the HEAD SHA,
and “P0/P1 first; Approve / Request changes.”

---

## Leftovers that are OK to leave vs must fix

**OK until a later WP**

- `CascadeResolver` still T0-auto-merges (I.5).
- `judge_pair` lemma equality (I.3).
- `entity_types` table + core-manifest type seed (unused; plan said type seed
  unused).
- `UnregisteredEntityTypeError` still exported from `spine/resolver.py` /
  `spine/__init__.py` (D86 leftover; no remaining callers that mint by type).
- `test_e3_unknown_entity_type_gate.py` is a skipped stub plus shared
  recording doubles imported by I.1 tests. Rewrite as D96 discard proofs
  (called out as I.2 follow-up, not a merge blocker if CI is green).
- `golden_pairs.entity_type` column still exists (I.3).
- Query-space public columns `entity_type` / `type_confidence` / `emitted_type`
  vacated NULL on purpose.

**Must not**

- Reintroduce types or D18 as a write gate.
- Ship exact-T0 auto-merge or enable it because the corpus is large.
- Optimize for LoCoMo.
- Add expand/contract or mixed-generation E3 drain (operator waived BC).
- Cite analysis or `optional-exact-t0-accept.md` as settled architecture.
- Leave secret values in git (this repo is RememberStack OSS; still no
  tokens/PEMs).

---

## How to continue in git

```bash
cd /Users/jpuc/code/moje/remember-stack
git fetch origin
git checkout feat/wp-i2-type-cut
git pull --ff-only origin feat/wp-i2-type-cut
# confirm clean
git status
gh pr view 311
```

After I.2 squash-merges:

```bash
git checkout main
git pull origin main
git checkout -b feat/wp-i3-judge-pair   # or I.4 profile; they may be parallel
```

New implementation PRs: branch from updated `main`, dual-review, squash-merge.
I.3 and I.4 can be separate PRs.

---

## File map (cold start)

| Topic | File |
|---|---|
| Binding design | `plan/designs/entity_identity_and_retrieval_design.md` |
| Plan / WP table | `plan/plans/entity_identity_and_retrieval.md` |
| Decisions | `decisions.md` D95–D97 |
| Exact-T0 proposal | `design/proposals/optional-exact-t0-accept.md` |
| Bare nouns | `src/rememberstack/spine/entity_eligibility.py` |
| EntityRef | `src/rememberstack/model/relations.py` |
| E3 | `src/rememberstack/workers/e3.py` |
| Resolver (T0 still merges) | `src/rememberstack/spine/resolver.py` |
| Type-cut migration | `src/rememberstack/spine/migrations/versions/p9_14_0035_drop_entity_type.py` |
| Eval | `src/rememberstack/eval/resolution.py` |
| P3 paths | `src/rememberstack/workers/p3.py` |
| Graph queries | `src/rememberstack/surfaces/graph_queries.py` |
| Envelope GraphNode / EntityCandidate | `src/rememberstack/model/envelope.py` |
| Bootstrap | `src/rememberstack/spine/deployment_bootstrap.py` |
| Catalog contract | `src/rememberstack/spine/catalog_contract.py` |
| Query-space catalog | `src/rememberstack/spine/query_space/catalog.py` |
| Manifest | `src/rememberstack/spine/query_space/memory_v1_manifest.json` |
| LoCoMo pins | `benchmarks/locomo/protocol.py` |
| I.1 tests | `src/tests/spine/test_entity_eligibility.py`, `src/tests/workers/test_e3_bare_head_noun.py`, `src/tests/spine/test_resolver.py` |
| D86 stub | `src/tests/workers/test_e3_unknown_entity_type_gate.py` |

Regenerate query-space manifest after DDL/catalog contract edits:

```bash
uv run python -c "from rememberstack.spine.query_space import build_manifest, write_manifest; write_manifest(build_manifest())"
```

Then pin `EXPECTED_SURFACE_MANIFEST_HASH` and the LoCoMo runner fingerprint
test (run `test_single_run_summary_json_is_unchanged` and copy the new hash).

---

## Session scratch (not git)

`/var/folders/wt/plp93ggs40586mdsvzzqy4c40000gp/T/grok-goal-6edec73546f9/implementer/`

Contains review prompts, r1/r2 stdout, a stale `dual-reviews.json` that still
says PR #308 is OPEN (it is merged). Prefer `design/reviews/` and this handoff
over that JSON.

A Codex r2 process may still be running against `4b7efcdb`; ignore or kill it
and start a fresh review of HEAD.

---

## Suggested first hour for the next agent

1. `gh pr checks 311`. If surfaces is red, fix INSERTs / GraphNode only; do
   not reopen the type-cut design.
2. Dual-review HEAD (Codex + agy). File r3 under `design/reviews/`. Push.
3. Squash-merge #311.
4. Open WP-I.3 (`judge_pair`) and/or WP-I.4 (profile refresher) as new PRs
   from `main`. Dual-review. Merge both before touching T0 in `resolver.py`.
