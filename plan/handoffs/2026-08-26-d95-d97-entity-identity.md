# Handoff — D95–D97 entity identity and retrieval

**Written:** 2026-08-26, end of a Grok session that ran out of usage quota.
**For:** the next agent or human. Assume you were not in the room.

This file is a **session handoff**, not architecture. Binding design is
elsewhere (named below). If this document and a binding design disagree,
the design wins.

---

## 60-second orientation

RememberStack is the open-source memory engine. This program is **not**
the UMC cloud product. All of the work below lives in

`/Users/jpuc/code/moje/remember-stack`

The Grok session was attached to the UMC workspace
(`/Users/jpuc/code/moje/ultimate_memory_cloud_2/ultimate-memory-cloud`)
and drove RememberStack as a sibling checkout. UMC has **no uncommitted
D95–D97 code**. Do not look there for this program.

**The live implementation PR is**
[#311](https://github.com/writeitai/remember-stack/pull/311)
(`feat/wp-i2-type-cut` → `main`). Every type-cut commit and this handoff
are on that branch and pushed.

**What is already on `main`:** the binding design (D95–D97), the written
rule that T0 never auto-merges (exact-hit stays an unchosen proposal),
and WP-I.1 (bare-noun refusal + source/canonical aliases).

**What is not done:** WP-I.2 is coded on #311 but not merged (needs CI
green on HEAD + dual-review Approve of *this* HEAD). WP-I.3 through I.7
have not been started. Production T0 still auto-merges exact lemmas.
`judge_pair` still returns true when two surfaces fold to the same lemma.

---

## What the operator asked for

The program is: implement the accepted entity-identity design (D95–D97)
as dual-reviewed PRs, in the order of
`plan/plans/entity_identity_and_retrieval.md`.

Constraints the operator repeated and that later agents must keep:

- Analysis is not binding. Do not cite `plan/analysis/` as architecture.
- No LoCoMo optimization. Do not twist ingest or retrieval to chase a
  benchmark score.
- No backward compatibility. One hard cut: migrate, bump component
  versions, rebuild P2/P3, ship. No dual writers, no nullable
  `entities.type` for mixed binaries, no drain of old E3 generations.
- No reintroducing entity types, hats, or exact-T0 auto-merge.
- Dual review on every implementation PR: Codex `gpt-5.6-sol` at
  `xhigh` reasoning, and Antigravity `agy`. Both must **Approve** before
  squash-merge to `main`.
- CLA checkbox in the GitHub PR body (exact wording already on #311).
- CLAUDE.md Rule 2: design the full scope; sequencing belongs in
  `plan/plans/`, not as “v1 / later / MVP” inside a design.

A mid-session question was: *could exact-hit T0 stay in the code, off by
default, and be turned on only after a large corpus exists?* The answer
that was written down: **no, not with that trigger.** A large entity
table makes exact-lemma collision *worse* (birthday paradox), not safer.
An optional `t0_exact_accept` flag exists only as an **unchosen
proposal** (`design/proposals/optional-exact-t0-accept.md`), default off,
and is **not** WP-I.5. “Enable it after a large corpus” is a rejected
trigger. Do not ship the flag.

---

## The problem in plain language

RememberStack resolves mentions of people, products, orgs, etc. into
stable `entity_id`s. The **old** cascade treated an exact match on the
folded spelling (`aliases.normalized_lemma`) as a verdict at confidence
1.0. That is T0.

That made father and son impossible. If the father is stored as `Jan`
and the son is also called `Jan` in a later claim, T0 glued them. The
second person never reached a profile comparison (T3) or a judge (T4).
Every later observation, relation, graph hop, and forget then attached
to the wrong id. That is much more expensive than one extra embedding
lookup.

**D95** says identity is the real-world referent, not the spelling. T0
must **list candidates** (distinct active `entity_id`s that share the
lemma). It must not merge. Repeats of a *known, profiled* person are
cheap via **T3** (mention+claim embedding versus that entity’s profile
vector). Empty profile, conflicting profile, or several exact candidates
go to **T4**. Same lemma may mint a second id.

**D96** says we do not keep an 8-type ontology as identity. There is no
`bank` type that would split “the river bank” from “the financial bank.”
Those are different referents discovered from observation prose (“is a
bank” vs “lives in Prague”), not from a class column. Domain/range
(`predicate_signatures`, D18, D86 unknown-type retry) is not a write
gate. `works_for(Alice, Me)` must persist. Unknown predicates still
follow D5 (`other:`).

**D97** says default retrieval is not “search the whole corpus for the
string.” It is: resolve the name to an id → look up that id’s
observations and relations → walk the graph neighborhood with **empty
predicates** (so `other:*` edges count) → constrain fact-text search to
those ids. No type filter. No new LLM on the query path.

Hats (a mention wearing several types) were considered and rejected.
Types-as-identity was the thing being removed, not replaced with another
classification scheme.

---

## Binding documents (read these, in this order)

1. `CLAUDE.md` / `AGENTS.md` — working agreement, Rule 2.
2. `decisions.md` — **D95, D96, D97** (canonical numbered log).
3. `plan/designs/entity_identity_and_retrieval_design.md` — binding how.
   Especially §3 (cascade), §4 (extract/aliases), §8 (eval fixtures),
   §9 (hard type cut), §11 (tests).
4. `plan/plans/entity_identity_and_retrieval.md` — WP-I.1 … I.7 order.
   This is sequencing, not design.

Useful but **not binding**:

- `plan/analysis/entity_identity_and_retrieval_analysis.md` — why.
- `design/proposals/optional-exact-t0-accept.md` — unchosen flag.
- Dual-review files under `design/reviews/REVIEW_*wp_i1*` and
  `REVIEW_*wp_i2*`.

---

## What is already merged to `main`

`origin/main` when this was written pointed at WP-I.1:

`dc6eae4b feat(e3): WP-I.1 bare-noun refusal and source/canonical aliases (#308)`

| PR | What landed | When |
|---|---|---|
| [#304](https://github.com/writeitai/remember-stack/pull/304) | D95–D97 design | 2026-08-26T14:11:06Z |
| [#307](https://github.com/writeitai/remember-stack/pull/307) | T0-never-merge written into the design; exact-T0 stays a proposal | 2026-08-26T14:43:32Z, squash `ed7bff50` |
| [#308](https://github.com/writeitai/remember-stack/pull/308) | **WP-I.1** | 2026-08-26T19:11:43Z, squash `dc6eae4b` |

### WP-I.1 in one paragraph

The extractor used to mint bare nouns (`game`, `company`) as entities,
and it had no way to record that the claim said `App` while the
nominative form is `Application`. I.1 refuses a closed list of bare
head nouns (`src/rememberstack/spine/entity_eligibility.py`) and adds
`EntityRef.surface`. Aliases: `llm_canonical` for the nominative, and
`source` **only when that spelling actually appears in the claim**
(`surface_appears_in_claim`, word-bounded, case-insensitive). Codex r1
caught two P1s: ungrounded `source` aliases, and tests that did not
drive the shipped E3+resolver. Those are closed in r3 Approve.

`application` is **not** in the bare-noun list. Putting it there would
drop App/Application, which is a real product name we must keep.

I.1 also has a `generic_identifier_guard` **writer**. I.5 will use it to
downweight T1/T2 when a lemma already spans two entities. It is not a
T0 auto-merge.

**I.1 did not change T0.** Exact lemma still auto-merges in
`CascadeResolver.resolve` until WP-I.5. That is deliberate sequencing:
you cannot measure D95 if `judge_pair` still treats lemma equality as
true, and you cannot turn T0 into a candidate list while T3 still embeds
name-only (all Johns look the same).

Dual reviews for I.1 (Approve r3):

- `design/reviews/REVIEW_codex-sol_wp_i1_extract_aliases_r3_2026-08-26.md`
- `design/reviews/REVIEW_agy_wp_i1_extract_aliases_r3_2026-08-26.md`

---

## Open PR: WP-I.2 type cut — #311

**URL:** https://github.com/writeitai/remember-stack/pull/311
**Branch:** `feat/wp-i2-type-cut` (pushed, clean)
**Base:** `main`
**All implementation work from this session is on this PR.**

### Commits (oldest → newest)

| SHA | Subject |
|---|---|
| `a802ebb3` | `feat(er): D96 hard type cut on ingest, P2/P3, and resolve` |
| `cc485c5d` | `fix(er): replace entities_current before dropping type column` |
| `b4f2a382` | `fix(er): finish D96 type-cut consumers and view dependents` |
| `4b7efcdb` | `docs(review): file WP-I.2 r1 dual reviews of the incomplete cut` |
| `2b87db44` | `fix(er): restore non-entity INSERTs and D96 chain assertions` |
| `3639b976` | first (shorter) handoff |
| *(this file, next commit)* | expanded handoff |

Re-read `git log origin/main..HEAD` after you pull; more CI-fix commits
may exist.

### What I.2 is trying to do

Drop entity type as identity in **one** release. Schema first in the
same PR, then writers. If you stop writing `type` before the migration
runs, INSERT dies on NOT NULL. If you drop the column while Python still
SELECTs `e.type`, everything else dies. Hence one PR, migration first.

Alembic revision `p9_14_0035` (down to `p9_13_0034` from I.1).

**Authority columns that go away**

- `entities.type` (NOT NULL, FK to `entity_types`)
- `entities.type_confidence`
- `mentions.emitted_type`
- `mentions.type_confidence`
- table `predicate_signatures`

**Public view columns that stay, vacated to NULL**

PostgreSQL will not `DROP COLUMN entities.type` while a view still
reads it. `CREATE OR REPLACE VIEW` cannot drop output columns either.
So `memory_v1.entities_current` keeps names `entity_type` and
`type_confidence` as `NULL::text` / `NULL::real`. Same idea for
`v_memory_mention_current_content.emitted_type` / `type_confidence` and
`v_graph_entities.type`. **Do not filter on those columns.** They are
always NULL. Filtering `entity_type = 'Person'` returns the empty set.

**Order inside `upgrade()`** (this is what made contract-smoke go green):

1. Replace `memory_v1.entities_current` (no longer reads `e.type`).
2. Replace `v_memory_mention_current_content` (no longer reads
   `m.emitted_type`).
3. Replace `v_graph_entities` (`NULL::text AS type`).
4. Drop FK, drop `ix_entities_type`, drop the four columns, drop
   `predicate_signatures`.

The first I.2 attempt only replaced `entities_current` and still failed
with `DependentObjectsStillExist` because of the mention helper and
`v_graph_entities`.

Downgrade must restore the **provenance EXISTS** subquery on
`entities_current`, not the short `WHERE e.status = 'active'` form.
Agy r1 caught that as P1.

### Runtime cut (already on the branch)

- `EntityRef`: `name` + optional `surface`. Keyword `type=` is forbidden
  (`extra="forbid"`); dict JSON still pops legacy `type`.
- `GraphNode`, `EntityCandidate`: no `type`. Graph Cypher projects
  `b.id, b.name, length(r) AS hops` (not `b.type`). Path-row indexes
  shifted left by one.
- E3 version
  `e3-normalize-2026.08c:temp0-1:claim-fanout-1:bare-noun-1:no-types-1`.
  No D86 retry, no domain/range. Unknown predicates dropped unless
  `other:`.
- Bootstrap still inserts core `entity_types` and `predicates` (unused
  seed). `predicate_signatures_count=0`. Pack install does not write
  signatures.
- P2 Entity nodes untyped (`p2-rebuild-2026.08`). P3
  `entities/<entity_id>/` (`p3-corpusfs-2026.08`). Entity-page links to
  documents are `../../documents/` (one `../` less than the typed path).
- `resolve` has no `entity_type` argument (HTTP, SDK, assured ops, LoCoMo
  tool dispatcher).
- Forget no longer `SET type_confidence`.
- Query sandbox entity filter allowlist is empty.

### Pins that will drift if you touch DDL or the query-space catalog

| Pin | Value at `2b87db44` (re-check after further commits) |
|---|---|
| Alembic head | `p9_14_0035` |
| Table count | 72 |
| `EXPECTED_CONSTRAINT_COUNTS` | `c=68, f=127, n=548, p=72, u=36, x=1` |
| Surface manifest SHA-256 | `58a6e5646c8d1fe96ec4e30b87031fc8a100e2f7e59749bc396aa3c8cdf192a3` |
| LoCoMo protocol fingerprint (runner test) | `9e7609222975165ada2f4871ff7c5af92d76abb0de288bb829a296b583592844` |

After any view/catalog change:

```bash
uv run python -c "from rememberstack.spine.query_space import build_manifest, write_manifest; write_manifest(build_manifest())"
```

Then update `benchmarks/locomo/protocol.py` `EXPECTED_SURFACE_MANIFEST_HASH`
and the literal in
`src/tests/benchmarks/test_locomo_runner.py::test_single_run_summary_json_is_unchanged`
(run it once; it prints the new fingerprint).

View comments on public relations must be **>200 characters**, start with
a capital, end with a period. A short “vacated after D96” comment on
`entities_current` failed that gate; the original long comment was kept
and a vacated sentence appended.

### Dual review of I.2

| Round | Codex | agy | Against |
|---|---|---|---|
| r1 | Request changes | Request changes | incomplete cut (`cc485c5d`) |
| r2 | never filed (process killed when the session stopped) | **Approve** | `4b7efcdb` |
| next | **must re-run on current HEAD** | re-run or delta vs r2 | current HEAD |

Filed:

- `design/reviews/REVIEW_codex-sol_wp_i2_type_cut_r1_2026-08-26.md`
- `design/reviews/REVIEW_agy_wp_i2_type_cut_r1_2026-08-26.md`
- `design/reviews/REVIEW_agy_wp_i2_type_cut_r2_2026-08-26.md`

**Do not merge on r1.** r1 P0s (graph `GraphNode.type`,
`predicate_signatures` writers, DROP COLUMN dependents) are addressed in
later commits. Re-review HEAD.

### CI (re-check; this snapshot goes stale)

On `2b87db44`: contract smoke, quality, unit, adapters, workers had
passed. Surfaces was still running. The handoff commit `3639b976` kicked
a new run (docs-only, should be the same tests).

```bash
gh pr checks 311
```

### Mistakes already paid for — do not repeat

1. **DROP COLUMN before replacing every dependent view.** Postgres error
   is `DependentObjectsStillExist` with no useful DETAIL in CI. The
   dependents were `v_graph_entities` and
   `v_memory_mention_current_content`, not only `entities_current`.
2. **GraphNode `extra="forbid"`.** Envelope dropped `type`;
   `graph_queries.py` still passed `type=`. Pyright and runtime both
   fail.
3. **Global regex to strip `'Person'` from VALUES.** It also ate
   `'Person'` from `golden_pairs.entity_type`, `'Task'`/`'Concept'` from
   `entity_types` INSERTs, and `'document'` from `processing_state`
   (`IGNORECASE` matched `Document`). Symptom: `INSERT has more target
   columns than expressions`. Restored in `2b87db44`.
4. **Entity INSERT column list without the VALUES slot.** Symptom:
   `INSERT has more expressions than target columns`. The leftover was
   often `:kind` or `:type`, not `'Person'`, so a literal-only regex
   missed it (`test_retrieval_batch_c.py`, `_d.py`, spikes).
5. **D18-violating canned relation in E3 chain tests.** The payload
   included `works_for(Quarterly Report, Acme)` expecting D18 to drop it.
   After D96 it lands, so relation counts and P1 channel counts moved.
   That candidate was removed from `_NORMALIZATION_PAYLOAD`; the
   signature-gate test was rewritten as
   `test_works_for_persists_without_a_type_gate`.
6. **P3 relative links.** Typed path was three levels deep
   (`entities/organization/<id>/` → `../../../documents/`). Untyped is
   two (`../../documents/`).
7. **`CREATE OR REPLACE VIEW` and column comments.**
   `_VIEW_START` in `migrations/_helpers.py` originally matched only
   `CREATE VIEW`, not `CREATE OR REPLACE VIEW`. TYPE_CUT uses OR REPLACE.
   Public comments still come from earlier migrations unless you emit
   `COMMENT ON COLUMN`. Safer: do **not** put short `--` comments on the
   TYPE_CUT column list, so authored comments stay the long p9_01 ones
   and live comments still match.

### Merge recipe

1. `gh pr checks 311` all green on HEAD.
2. Dual-review Approve on **that** HEAD. Commit review files as
   `design/reviews/REVIEW_{codex-sol,agy}_wp_i2_type_cut_r3_2026-08-26.md`.
3. CLA checkbox is already in the body.
4. Squash-merge:

```bash
gh pr merge 311 --squash --delete-branch
```

---

## After I.2: I.3 and I.4 (parallel), then I.5

From `plan/plans/entity_identity_and_retrieval.md`. I.3 and I.4 may be
**developed** in parallel after I.2. Both must **merge before I.5**.
I.6 can be coded against fixtures after I.2; it **ships** after I.5.

### WP-I.3 — `judge_pair`

File: `src/rememberstack/eval/resolution.py`.

Today, still:

```python
if lemma_a == lemma_b:
    return True, "T0"
```

Same-lemma **non-matches** (father/son, two companies named SAP) are
invisible. D95 cannot be scored.

Change that. Keep a global P/R curve **and** per-tier diagnostics (do
not delete the deciding tier). Golden pairs need not be keyed by
`entity_type`. Land design §8 fixtures (same-name non-match,
empty-profile John). Suite must run without types.

I.3 is **not** “turn T0 into a candidate list.” That is I.5, and I.5
needs a **recorded passing I.3+I.4 eval run**.

### WP-I.4 — profile refresher + T3 on name+profile

There is no real profile worker yet. `REFRESH_PROFILE` is an unused
pipeline enum. Without profiles, T3 embeds the spelling; every `Jan`
looks the same, so turning off T0-merge just moves the glue to T3.

Need: a refresher (on observation-flush or a `ProfileRefresherHandler`);
T4 prompt that sees profile + salient observations; T3 vectors of
name+profile; debounce on evidence change; **D74** forget of one
document on a **shared** entity rebuilds the profile (forgotten
distinctive phrase gone from summary, inputs, vector, search). Empty
profile is fail-safe (do not T3-accept).

Acceptance: “is a bank” / “lives in Prague” show up in T4; two same-name
vectors differ once profiles differ; shared-survivor forget test green.

### WP-I.5 — T0 lists candidates, never merges

Production code today (`src/rememberstack/spine/resolver.py` around the
`exact = connection.execute(_T0_EXACT, ...)` block):

```python
if exact is not None:
    return self._record(..., method="T0", confidence=1.0, created=False)
```

Replace with: T0 returns the list of distinct active ids. One profiled
candidate may T3-accept. Empty / conflict / many → T4. Same lemma may
mint. `resolution_exclusions` on T4 no-match. `generic_identifier_guard`
when a lemma spans ≥2 entities (blocking, not a T0 verdict).

Acceptance: father/son → two ids via T4; empty-profile second `Jan` →
T4 not T0 merge; repeat profiled `James` → T3 without T4; SAP shorthand
→ one id via T3/T4 not T0.

**Do not implement `t0_exact_accept`.**

### WP-I.6 — D97 default retrieval

`resolve` → lookup observations+relations → `neighborhood` with empty
predicates → ID-constrained fact-text search. Files:
`assured_operations.py`, `operation_executor.py`, `query_engine.py`.
Hop must return `other:*` neighbors. Observations via lookup, not as
graph nodes. No new query-path LLM. “List banks” matches observation /
profile text. Ambiguity, missing P2, and caps are explicit negatives.

### WP-I.7 — docs site (D66)

User-visible behavior only, on the same PR as the WP it documents,
under `website/src/app/docs/**`.

---

## Dual-review commands (copy exactly)

Cwd: `/Users/jpuc/code/moje/remember-stack`. Reviewers must not edit
tracked files. Write to a scratch path, then copy into
`design/reviews/`.

```bash
codex exec --dangerously-bypass-approvals-and-sandbox \
  --model gpt-5.6-sol \
  -c 'model_reasoning_effort="xhigh"' \
  -C /Users/jpuc/code/moje/remember-stack \
  "$(cat /path/to/prompt.txt)"

agy --dangerously-skip-permissions --print-timeout 180m0s \
  -p "$(cat /path/to/prompt.txt)"
```

Prompt must include: PR number, `origin/<branch> vs origin/main`, HEAD
SHA, “P0/P1 first”, verdict `Approve` or `Request changes`, and “do not
modify tracked files.” For a later round, name the previous review files
and ask for a closure audit.

Scratch from this session (not git, may already be gone):

`/var/folders/wt/plp93ggs40586mdsvzzqy4c40000gp/T/grok-goal-6edec73546f9/implementer/`

`dual-reviews.json` there still claims #308 is OPEN. It is merged. Prefer
this handoff and `design/reviews/`.

---

## Leftovers

**Leave until the WP that owns them**

| Leftover | Owner |
|---|---|
| T0 exact still auto-merges | I.5 |
| `judge_pair` lemma equality | I.3 |
| `entity_types` table + core seed | unused; plan allowed it |
| `UnregisteredEntityTypeError` still exported | dead D86; delete when convenient |
| `test_e3_unknown_entity_type_gate.py` skipped stub + shared test doubles | rewrite as D96 proofs; I.1 tests import the doubles |
| `golden_pairs.entity_type` column | I.3 |
| Vacated public type columns | intentional |

**Never do**

- Reintroduce types or D18 as a write gate.
- Ship or enable exact-T0 because the corpus is large.
- Optimize for LoCoMo.
- Expand/contract or mixed-generation E3 drain.
- Cite analysis or the optional-T0 proposal as settled architecture.

---

## Git: pick up where this left off

```bash
cd /Users/jpuc/code/moje/remember-stack
git fetch origin
git checkout feat/wp-i2-type-cut
git pull --ff-only origin feat/wp-i2-type-cut
git status          # should be clean
gh pr view 311
gh pr checks 311
```

After #311 squash-merges:

```bash
git checkout main && git pull origin main
git checkout -b feat/wp-i3-judge-pair     # and/or feat/wp-i4-profile
```

---

## File map

| Topic | Path |
|---|---|
| Binding design | `plan/designs/entity_identity_and_retrieval_design.md` |
| Plan | `plan/plans/entity_identity_and_retrieval.md` |
| Decisions | `decisions.md` D95–D97 |
| Exact-T0 proposal | `design/proposals/optional-exact-t0-accept.md` |
| Bare nouns | `src/rememberstack/spine/entity_eligibility.py` |
| EntityRef | `src/rememberstack/model/relations.py` |
| E3 | `src/rememberstack/workers/e3.py` |
| Resolver (T0 still merges) | `src/rememberstack/spine/resolver.py` ~89–130 |
| Type-cut migration | `src/rememberstack/spine/migrations/versions/p9_14_0035_drop_entity_type.py` |
| Eval / `judge_pair` | `src/rememberstack/eval/resolution.py` |
| P3 | `src/rememberstack/workers/p3.py` |
| Graph queries | `src/rememberstack/surfaces/graph_queries.py` |
| Envelope | `src/rememberstack/model/envelope.py` |
| Bootstrap | `src/rememberstack/spine/deployment_bootstrap.py` |
| Catalog contract | `src/rememberstack/spine/catalog_contract.py` |
| Query-space catalog | `src/rememberstack/spine/query_space/catalog.py` |
| Manifest | `src/rememberstack/spine/query_space/memory_v1_manifest.json` |
| LoCoMo pins | `benchmarks/locomo/protocol.py` |
| I.1 tests | `src/tests/spine/test_entity_eligibility.py`, `src/tests/workers/test_e3_bare_head_noun.py`, `src/tests/spine/test_resolver.py` |
| D86 stub | `src/tests/workers/test_e3_unknown_entity_type_gate.py` |
| E3 chain (D96 `works_for`) | `src/tests/workers/test_e3_chain.py` |

---

## Suggested first hour

1. Confirm #311 HEAD is pushed and `git status` is clean.
2. `gh pr checks 311`. If surfaces/workers are red, fix test INSERTs or
   assertions only. Do not reopen D96.
3. Dual-review **this** HEAD (Codex + agy). File r3. Push.
4. Squash-merge #311.
5. Start I.3 (`judge_pair`) and I.4 (profile) as new PRs from `main`.
   Do not edit `resolver.py` T0 until both have merged and an eval run
   is recorded.
