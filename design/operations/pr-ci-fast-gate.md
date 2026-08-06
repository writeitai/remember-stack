# PR CI fast gate (target &lt;10 minutes)

Status: **proposed implementation** (see `.github/workflows/ci.yml` and
`ci-nightly.yml`). Not a claim that every historical flake is fixed.

## Problem

PR CI ran the full suite on Python 3.12/3.13/3.14 with coverage (~45–55 min
wall). Design-only and narrow code PRs paid the same cost. A flake on a
non-required matrix cell (3.14) skipped the required Coverage job and blocked
merge.

## Decision

| Lane | When | Contents | Required for merge |
| --- | --- | --- | --- |
| **PR gate** | every PR / push to main | ruff, format, import lint, pyright @ 3.13; unit path pack (no Postgres); contract-smoke (Postgres + migrations + skeleton + a few pure contracts) | **Yes** (check name `PR gate`) |
| **Path integration** | PR when source paths match | Postgres integration suites for workers / surfaces / shared-core expansion | No (soft; failures still visible) |
| **Compose quickstart** | PR when compose/Docker/selfhost paths match | existing compose cold-start gate | No |
| **Nightly / workflow_dispatch** | schedule + manual | full matrix 3.12–3.14, full `pytest src/tests --cov`, coverage report, compose quickstart | Release confidence, not PR merge |

CLA remains a separate required check.

## Path map

See `.github/ci/path-filters.yml`. Unit vs integration file lists:
`.github/ci/unit-paths.txt`, `.github/ci/integration-paths.txt` (heuristic:
files that reference Postgres/Alembic/engine fixtures). **Shared-core** paths
expand to both workers + surfaces integration packs.

## Explicit non-goals

- Fixing AGE INT128 / graph flakiness (tracked separately).
- Perfect path→test dependency analysis (would need import-graph tooling).
- Requiring coverage on every PR.

## Costs / risks

- A change that only breaks an unselected integration file can merge green and
  fail nightly. Mitigation: shared-core expansion, contract-smoke skeleton, and
  nightly alerts on `main`.
- Unit path list can drift; regenerate with the same heuristic when adding
  suites and keep contract-smoke intentionally small.

## Branch-protection cutover (required with this change)

Current required contexts (before): `checks (3.12)`, `checks (3.13)`,
`Coverage report`, `CLA`.

After this workflow lands on `main`, PRs no longer emit the old matrix or
Coverage contexts. Cutover procedure:

1. Open the PR that adds this workflow; do **not** remove old required contexts
   until `PR gate` has reported success at least once on that PR.
2. In branch protection / ruleset for `main`, **add** required check `PR gate`
   (display name; job `name: PR gate` in `ci.yml`). Keep `CLA`.
3. **Remove** required checks `checks (3.12)`, `checks (3.13)`, and
   `Coverage report` only after step 2 is saved.
4. Confirm a green PR can merge; confirm a red `PR gate` cannot.
5. Manually dispatch **Nightly CI** once on `main` after merge.
6. Rollback: restore the previous `ci.yml` matrix jobs that emit the old check
   names **before** re-adding those contexts to protection (otherwise merges
   wait forever).

Owner: repository admins performing the ruleset edit. Soft integration failures
do not block merge; nightly is the backstop (Actions failure visibility; formal
alerting is a follow-up).

## Follow-up

- pytest markers (`unit` / `pg` / `graph`) for finer control.
- Diff-cover on PR instead of full coverage.
- Nightly failure notification (issue / chat) with an owner SLA.
- Drop soft integration from PR once local pre-push is standard.
