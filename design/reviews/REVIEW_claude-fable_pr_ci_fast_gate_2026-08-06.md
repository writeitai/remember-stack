# Review: PR CI fast gate

**Reviewer:** claude-fable (adversarial design + CI dual review)  
**Date:** 2026-08-06  
**Branch:** `ci/pr-fast-gate`  
**Scope:**

| Artifact | Path |
| --- | --- |
| Design | `design/operations/pr-ci-fast-gate.md` |
| PR workflow | `.github/workflows/ci.yml` |
| Nightly workflow | `.github/workflows/ci-nightly.yml` |
| Path filters | `.github/ci/path-filters.yml` |
| Unit pack | `.github/ci/unit-paths.txt` |
| Contract smoke pack | `.github/ci/contract-smoke-paths.txt` |
| Integration inventory | `.github/ci/integration-paths.txt` |

**Context (as given):** Old PR CI ≈45–55 min (full pytest + cov on 3.12/3.13/3.14). Goal ≈&lt;10 min PR wall time; full multi-Python + coverage on nightly. Current branch protection: `checks (3.12)`, `checks (3.13)`, `Coverage report`, `CLA`. Unit pack claimed ~357 tests / ~43s locally.

---

## 1. Verdict

**Accept with changes.**

The control-plane shape is correct and worth shipping:

- One **always-present** aggregate check (`PR gate`) that only hard-depends on quality + unit + contract-smoke (`.github/workflows/ci.yml:379-393`) is the right answer to matrix/coverage skip-flake merge blocks.
- Pinning the hard lane to **3.13 only**, dropping PR coverage, and moving the multi-Python + `--cov` matrix to nightly (`.github/workflows/ci-nightly.yml`) matches the stated cost goal.
- Soft path-filtered integration + compose (visible but non-blocking) is an explicit, honest risk trade — **if** the hard packs and path map actually cover what the design claims.

They do not, yet. Several pure contract tests never run on any PR job; `integration-paths.txt` is unused theater; the path map has large source holes; and branch-protection cutover can brick or open the repository if done wrong. Fix the P0 items **before** flipping required checks, or accept a measurable false-green window.

---

## 2. Summary

| Claim in design | Reality in branch |
| --- | --- |
| PR gate = quality + unit + small contract-smoke @ 3.13 | **Matches** `ci.yml` jobs `quality`, `unit`, `contract-smoke`, aggregate `pr-gate` named `PR gate` |
| Contract-smoke is migrations + skeleton + a few contracts | **Matches** `.github/ci/contract-smoke-paths.txt` (8 modules) |
| Unit pack = heuristic non-Postgres files | **Incomplete** — pure modules live only on the integration inventory / nowhere on PR |
| Path integration soft-runs PG suites when sources match | **Partial** — soft jobs use **directory globs**, not `integration-paths.txt`; whole classes of tests never soft-run |
| Shared-core expands blast radius | **Too narrow** — many spine modules trigger neither workers nor surfaces nor shared_core |
| Nightly = release confidence | **Mostly** — full `pytest src/tests --cov` recovers unlisted files; compose nightly is a **weaker** subset of PR compose |
| Nightly alerts on `main` | **Not implemented** — schedule only, no failure notification |

Net: this will almost certainly hit the wall-time goal (hard lane is quality ∥ unit~43s ∥ 8-file PG smoke, timeout 20m on smoke). Merge protection quality drops from “full suite on PR” to “lint + pure-ish unit list + tiny PG smoke,” with soft reds humans can ignore. That is fine **only** if unit/contract lists are complete and branch protection is migrated carefully.

---

## 3. Findings

### P0 — must fix or consciously accept before protection flip

#### P0-1. Pure tests never execute on any PR job (hard or soft)

Hard PR coverage is **only**:

1. `unit-paths.txt` (47 files) — always  
2. `contract-smoke-paths.txt` (8 files) — always  
3. Soft dirs when path filters fire:
   - `integration-workers` → `src/tests/workers` + `src/tests/spine` (`ci.yml:157-158`)
   - `integration-surfaces` → `src/tests/surfaces` + `src/tests/eval` (`ci.yml:196-197`)
   - `compose-quickstart` when compose paths match  

`.github/ci/integration-paths.txt` is **never read** by either workflow. Any module listed there (or missing from unit) that is **not** under those four dirs is **nightly-only**.

Concrete pure / no-DB modules that do **not** appear in `unit-paths.txt` and are **outside** soft job dirs:

| Module | Why it matters |
| --- | --- |
| `src/tests/core/test_section_snap.py` | WP-3.3 section-snap invariants / fuzz; pure `core` contract |
| `src/tests/test_queue_port_contract.py` | D67 queue signature / stage vocabulary contract |
| `src/tests/test_port_model_values.py` | Provider-boundary value invariants |
| `src/tests/profiles/test_selfhost_profile.py` | Supported worker stages vs profile composition |

A PR that only breaks `test_section_snap` or `test_queue_port_contract` can be **fully green** on `PR gate` and on soft integration (filters never select those files). Nightly catches it hours later on `main`.

Likely root cause of `test_section_snap.py` mis-binning: the fixture document string contains `"Deployments run one Postgres each."` — a dumb `Postgres` grep heuristic false-positive. Design admits the heuristic (`pr-ci-fast-gate.md:27-28`); it does not admit that false positives **remove** tests from the required unit lane with no fallback on PR.

**Required change:** Move all non-Postgres modules into `unit-paths.txt` (or run `pytest` with an inverse exclude / markers). Add a CI drift check: every `src/tests/**/test_*.py` is in unit ∪ contract-smoke ∪ (dirs covered by soft jobs), else fail the PR that adds the orphan.

#### P0-2. Adapter / profile / spike / root PG tests are never soft-selected

Also present in `integration-paths.txt` but never invoked by soft jobs:

- `src/tests/adapters/test_mount_provisioning.py`
- `src/tests/adapters/test_selfhost_queue.py`
- `src/tests/spikes/test_*.py` (3 modules)
- plus the pure orphans above  

Changing `src/rememberstack/adapters/selfhost/queue.py` or mounts:

- Does **not** match `workers` / `surfaces` / `shared_core` / `compose` filters (adapters are absent from `path-filters.yml`).
- Does **not** expand contract-smoke (fixed 8-file list).
- Unit may cover some adapter pure tests, not the PG shell proofs.

So adapter PG regressions are **nightly-only** unless someone hand-updates path lists. That is a merge-protection gap larger than the design’s “unselected integration file” disclaimer, because these files are not merely soft-optional — they are **unreachable** on PR.

**Required change:** Either (a) soft job(s) consume `integration-paths.txt` (or an `adapters` path filter + `pytest src/tests/adapters`), or (b) fold critical adapter PG proofs into contract-smoke, or (c) document them as intentionally nightly-only with owners.

#### P0-3. Branch-protection cutover can brick merges or open the gate

Today (context): required = `checks (3.12)`, `checks (3.13)`, `Coverage report`, `CLA`.

After this branch:

| Old check name | Still produced on PR? |
| --- | --- |
| `checks (3.12)` / `checks (3.13)` | **No** — matrix job removed from `ci.yml` |
| `Coverage report` | **No** on PR — only nightly `coverage` job (`ci-nightly.yml:101-119`) |
| `CLA` | **Yes** (`cla.yml`, job name `CLA`) |
| `PR gate` | **Yes** — job `name: PR gate` (`ci.yml:380-381`) |

If protection is updated **out of order**:

1. **Remove old required checks before `PR gate` exists on `main`** → window with only CLA (or nothing).  
2. **Leave `Coverage report` required** → every PR blocked forever (nightly does not report that check on the PR SHA).  
3. **Require job id `pr-gate` instead of display name `PR gate`** → GitHub status mismatch; flaky required-check UX.  
4. **Require `Quality` / `Unit` / `Contract smoke` individually without the aggregate** → works, but loses the “single always-green-or-red required name” story; soft jobs must stay **non-required**.

`pr-gate` correctly uses `if: always()` and asserts `needs.*.result == success` (`ci.yml:384-393`), so a failed hard lane fails the aggregate even when siblings succeed. Good. Soft jobs are intentionally **not** in `needs` — failures there do **not** fail `PR gate`. That is by design (`pr-ci-fast-gate.md:18-19`) but is a **merge protection gap**: red `Integration (workers)` still merges if `PR gate` is green.

See §4 for ordered migration steps.

---

### P1 — correctness / false-green / path map

#### P1-1. Source path map holes in spine (and friends)

`path-filters.yml` `workers` includes only a **subset** of spine modules:

```29:38:.github/ci/path-filters.yml
workers:
  - 'src/rememberstack/workers/**'
  - 'src/rememberstack/spine/fact_catalog.py'
  - 'src/rememberstack/spine/claim_catalog.py'
  - 'src/rememberstack/spine/observation_adjudication.py'
  - 'src/rememberstack/spine/rank_embed_cache.py'
  - 'src/rememberstack/spine/work_ledger.py'
  - 'src/rememberstack/spine/reconcile.py'
  - 'src/rememberstack/model/**'
  - 'src/tests/workers/**'
  - 'src/tests/spine/**'
```

`surfaces` covers `surfaces/**`, `query_space/**`, `projection.py`, `resolver.py`, `core/**`.  
`shared_core` covers `model/**`, `ports/**`, `catalog_contract.py`, `work_ledger.py`, `workers/base.py`, `profiles/selfhost.py`.

**Not matched** by workers, surfaces, or shared_core (examples under `src/rememberstack/spine/`):

`admission.py`, `backfill.py`, `chunk_catalog.py`, `clustering.py`, `component_versions.py`, `consumption.py`, `deployment_bootstrap.py`, `document_catalog.py`, `entity_registry.py`, `extension_packs.py`, `forget.py`, `knowledge.py`, `lifecycle.py`, `operations.py`, `readiness.py`, `recipes.py`, `review.py`, `settings.py`, `supersession.py`, `sync.py`, plus most of `migrations/versions/*` beyond the always-on smoke.

Effect of editing e.g. `spine/supersession.py` or `spine/forget.py` on a PR:

- Hard: quality (src) + unit list (likely no supersession tests) + contract-smoke (no supersession module).  
- Soft: **no** workers/surfaces/shared_core → **no** `test_supersession.py` / forget catalog PG suite.  
- Green merge; full spine pack only on **push to main** (`github.event_name == 'push'` forces soft jobs, `ci.yml:125-127`) or nightly.

Push-to-main recovery is real but late: broken code is already on the default branch.

**Required change:** Prefer `src/rememberstack/spine/**` under `shared_core` or `workers`, or accept and document “spine edits are main+nightly risk.” Current partial file list looks precise while being wrong.

#### P1-2. Soft integration is not merge protection

Design table marks path integration **No** for required. Operationally:

- Reviewers and authors will treat a green required check as “CI passed.”  
- Soft red checks are easy to miss in the Checks UI when `PR gate` is green.  
- There is no workflow step that comments “soft integration failed” on the PR.

Acceptable only with culture + CODEOWNERS discipline, or promote selected packs (e.g. workers when `workers/**` changes) to hard `needs` of `pr-gate` once runtime budget is known.

#### P1-3. No drift guard for path lists

Nothing fails CI when:

- A new `test_*.py` is added and omitted from unit/contract lists.  
- A path is deleted but left in a list (pytest errors — fail closed, OK).  
- `integration-paths.txt` diverges from soft job dirs (already true today).

Without markers (`unit` / `pg`) or a generator script in CI, list drift is inevitable. Design follow-up mentions markers (`pr-ci-fast-gate.md:47-48`) — that should be near-term, not optional polish.

#### P1-4. Dead filter outputs imply a half-finished design

`changes` job exports `quality`, `unit`, `contract` (`ci.yml:27-29`) and `path-filters.yml` defines those keys, but **hard jobs never `needs: changes` and never `if:` on those outputs**. They always run. That is good for required-check stability, but:

- The filter file documents a path-gated quality/unit story that the workflow does not implement.  
- Future editors may “optimize” by gating `unit` on `needs.changes.outputs.unit` and accidentally make `PR gate` skip or go false-green when `changes` fails.

Keep always-on hard lanes; delete unused filter keys **or** comment in `path-filters.yml` that quality/unit/contract filters are informational only.

#### P1-5. Nightly compose is weaker than PR compose

PR `compose-quickstart` proves cold start, MinIO immutability, full 10-stage pipeline, upgrade gate to `p9_06_0027` (`ci.yml:218-369`).  
Nightly compose only cold-starts and tears down (`ci-nightly.yml:30-44`).

So the **strong** compose proofs run only when compose path filters match (or push to main). A subtle regression in upgrade-gate compose overlay that lands via a non-compose path never re-runs the strong proofs on schedule. Invert the asymmetry: nightly should be **superset**, not subset.

#### P1-6. “Nightly alerts” are aspirational

Design risk mitigation: “nightly alerts on `main`” (`pr-ci-fast-gate.md:41`). Workflow has `schedule` + `workflow_dispatch` only — no `permissions` for issues, no Slack/email, no `notify` job. Failures are easy to miss for a full business day (cron `17 5 * * *` UTC).

#### P1-7. Multi-version regressions delayed up to ~24h

Acceptable trade for speed; still a P1 product risk for 3.12/3.14 syntax or dependency skew. Release workflow still has its own suite (`.github/workflows/release.yml`) — good backstop for tags, not for every merge.

---

### P2 — YAML footguns, cost, polish

#### P2-1. `mapfile` + path files are bash-shaped

```81:84:.github/workflows/ci.yml
      - name: Unit pack (no Postgres service)
        run: |
          mapfile -t paths < .github/ci/unit-paths.txt
          uv run pytest "${paths[@]}" -q --tb=short
```

Ubuntu GHA default shell is bash — OK. Footguns:

- Blank lines or `#` comments in the path file become pytest args → collection errors or weird selects.  
- Empty file → `pytest` with zero paths may exit 5 / or collect nothing depending on pytest version — treat as fail-closed by asserting `[[ ${#paths[@]} -gt 0 ]]`.  
- No `set -euo pipefail` on the step (GHA usually fails on non-zero; still better explicit).

#### P2-2. `cancel-in-progress: true` (`ci.yml:12-14`)

Rapid pushes cancel in-flight `PR gate`. Transient “failed/cancelled” on the required check is normal; authors must wait for the latest run. Document in the design so nobody “re-runs old jobs” into a race.

#### P2-3. Parallel `uv sync` ×3–6

Hard three jobs each `uv sync --locked`; soft jobs add more. Cache helps (`setup-uv` enable-cache). Not wrong; largest wall-time risk for the &lt;10m goal is cold cache + pyright + PG image pull on contract-smoke, not unit.

#### P2-4. Contract-smoke overlaps unit on purpose

Shared modules (e.g. `test_smoke.py`, `test_rank_embed_cache.py`, model/deployment, openrouter) run twice on every PR. Cheap insurance; fine. Prefer markers later to avoid double cost as packs grow.

#### P2-5. Quality does not lint/typecheck `src/tests`

`ruff` / `pyright` only `src/` + `benchmarks/` (`ci.yml:59-64`, same on nightly). Pre-existing; tests can rot style/types without PR signal.

#### P2-6. Docs / design-only PRs still pay full hard lane

Always-on quality + unit + PG smoke means design-only PRs still boot Postgres for 8 modules. Acceptable; not the “design-only free” ideal in the problem statement.

#### P2-7. Nightly coverage job permissions

`contents: write` + `pull-requests: write` (`ci-nightly.yml:106-108`) for badge/comment action — on `schedule` there is no PR; ensure the action no-ops cleanly (known pattern for `py-cov-action`). Not a PR merge issue.

#### P2-8. `integration-paths.txt` naming

Name implies CI input; it is an orphan inventory. Rename to e.g. `integration-inventory.txt` or wire it up (see P0-2) to stop future agents “fixing” the wrong file.

---

## 4. Required branch-protection migration steps

Execute **after** this branch is on `main` and a green `PR gate` status has been observed on at least one commit.

1. **Inventory**  
   Settings → Branches → rule for `main` (and any other protected branches). Record exact required check names today: `checks (3.12)`, `checks (3.13)`, `Coverage report`, `CLA`.

2. **Land code first**  
   Merge `ci/pr-fast-gate` with temporary protection still requiring old checks **only if** those checks still exist on the branch tip. Prefer: merge with admin override **or** a short dual-running period is **not** available (old job names are gone). Practical sequence:
   - Merge the workflow change (admin if needed).  
   - Immediately open a no-op PR or push and confirm Checks show: `PR gate`, `Quality`, `Unit`, `Contract smoke`, path jobs, `CLA`.

3. **Add before remove**  
   - Add required status: **`PR gate`** (exact display name).  
   - Keep **`CLA`** required.  
   - Do **not** add soft jobs (`Integration (workers)`, `Integration (surfaces)`, `Compose quickstart`, `Path filters`) as required unless product wants them hard.  
   - Do **not** require nightly-only `Coverage report` on PRs.

4. **Remove obsolete required checks**  
   Remove `checks (3.12)`, `checks (3.13)`, `Coverage report` in the **same** settings edit as adding `PR gate`, or in the next minute — never leave Coverage required without a PR producer.

5. **Optional main-branch policy**  
   If `main` pushes should also be gated, require `PR gate` on the same rule (push path already runs hard jobs + all soft jobs via `event_name == 'push'`).

6. **Verify negative path**  
   On a throwaway branch, force-fail unit (or contract-smoke) and confirm `PR gate` is red and merge is blocked. Force-fail only soft integration and confirm merge is still allowed (documents the chosen risk).

7. **Nightly ownership**  
   Assign a human/oncall to read Nightly CI failures next business morning until automated alerts exist (P1-6).

8. **Update any docs/runbooks** that mention old check names (`checks (3.x)`, PR coverage).

---

## 5. Suggested follow-ups

| Priority | Item |
| --- | --- |
| Before protection flip | Complete `unit-paths.txt` (P0-1); decide fate of adapter/spike PG tests (P0-2); write migration checklist into design or ops doc (§4). |
| Immediate | CI drift job: every `test_*.py` classified; fail on orphans. |
| Immediate | Expand `shared_core` to `src/rememberstack/spine/**` (or document holes). |
| Short | pytest markers `unit` / `pg` / `graph`; generate path lists from markers. |
| Short | Nightly compose = full PR compose script (upgrade + pipeline), not cold-start only. |
| Short | Nightly failure notification (GitHub issue, email, or chat webhook). |
| Medium | Diff-cover or changed-file test selection instead of growing contract-smoke. |
| Medium | Delete or wire `integration-paths.txt`; delete unused path-filter keys. |
| Later | Drop soft PR integration once pre-push hooks are standard (design non-goal today). |
| Later | Revisit making path-matched integration a hard `needs` of `PR gate` if soft reds are ignored in practice. |

---

## 6. What is already solid (do not regress)

- Aggregate required check pattern with `if: always()` and explicit `success` tests (`ci.yml:379-393`).  
- Hard lanes independent of `changes` — path-filter outage cannot skip quality/unit/smoke.  
- Contract-smoke list is actually small (8 files) and includes `test_migrations.py` — matches design prose.  
- Unit job runs **without** Postgres service — fail-closed if a PG test is misclassified into unit (raises/skips visibly; prefer raise over silent skip for required lane).  
- Push to `main` forces soft integration packs (`event_name == 'push'`) — post-merge net on default branch is wider than PR.  
- Postgres service image digest-pinned; healthcheck present.  
- CLA remains a separate workflow/check.  
- Nightly `fail-fast: false` on Python matrix preserves signal across 3.12–3.14.  
- Design explicitly declines perfect dependency analysis and AGE flake fixes — scope is honest even where the path map is not.

---

## 7. Bottom line

Ship the **workflow architecture** (single `PR gate`, 3.13 hard lane, nightly full matrix + coverage). **Do not** flip branch protection until unit-pack orphans are fixed and adapter/nightly-only gaps are explicit. Treat current soft path filters as a **best-effort signal**, not a safety net — the safety net is contract-smoke + push-to-main soft packs + nightly, and two of those are delayed.

**Verdict: Accept with changes.**
