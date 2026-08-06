# Adversarial review: PR CI fast gate

## Verdict

**Request changes.**

The always-present `PR gate` aggregation is the right shape: it depends on the
three hard lanes and uses `always()` so a failed dependency cannot turn the
required check into a skipped success. Moving full multi-version coverage out
of merge latency is also reasonable. The change is not safe to roll out yet,
however, because the existing required `Coverage report` context is removed
from PRs without a migration plan, part of the declared integration inventory
is unreachable from PR CI, and the required gate does not fail when its path
selector fails.

## P0 findings

None.

## P1 findings

### P1.1 — Removing the old required context has no coordinated branch-protection migration

The problem statement says `Coverage report` is currently required
(`design/operations/pr-ci-fast-gate.md:7-11`). This change moves that job into a
workflow triggered only by `schedule` and `workflow_dispatch`
(`.github/workflows/ci-nightly.yml:6-10`,
`.github/workflows/ci-nightly.yml:101-119`), while PRs emit the new `PR gate`
context (`.github/workflows/ci.yml:379-393`). Therefore the rollout PR and every
subsequent PR will wait forever for the old required context unless an
administrator changes the protection/ruleset out of band. Removing the old
context before the new one is observed creates the opposite failure: a window
in which the intended hard lanes are not required.

This is an operational part of the change, not an optional follow-up. Add an
owner and an explicit cutover/rollback procedure to the design, and execute the
cutover as part of deployment. The concrete migration is below.

### P1.2 — The integration inventory is not connected to the PR integration jobs

The design presents `unit-paths.txt` and `integration-paths.txt` as the test
classification (`design/operations/pr-ci-fast-gate.md:24-29`). The unit job
does consume its manifest (`.github/workflows/ci.yml:80-83`), but neither
integration job consumes the integration manifest. They instead run only four
whole directories: `src/tests/workers`, `src/tests/spine`,
`src/tests/surfaces`, and `src/tests/eval`
(`.github/workflows/ci.yml:157-158`, `.github/workflows/ci.yml:196-197`).

Nine of the 62 files currently classified as integration tests are outside
those directories and are therefore never run by PR CI, even when the test
itself is changed:

- `src/tests/adapters/test_mount_provisioning.py`
- `src/tests/adapters/test_selfhost_queue.py`
- `src/tests/core/test_section_snap.py`
- `src/tests/profiles/test_selfhost_profile.py`
- `src/tests/spikes/test_operational_scale.py`
- `src/tests/spikes/test_p2_engine_spikes.py`
- `src/tests/spikes/test_retrieval_spikes.py`
- `src/tests/test_port_model_values.py`
- `src/tests/test_queue_port_contract.py`

The filters have no integration route for `src/rememberstack/adapters/**`,
`src/tests/adapters/**`, `src/tests/profiles/**`, `src/tests/spikes/**`, or the
top-level integration tests (`.github/ci/path-filters.yml:28-48`). The compose
selector also calls only `src/rememberstack/profiles/selfhost.py` a self-host
path and omits the rest of `src/rememberstack/profiles/**` and
`src/rememberstack/adapters/selfhost/**`
(`.github/ci/path-filters.yml:50-57`). A direct change to the Postgres queue
adapter or its integration test can consequently merge with no relevant
integration execution. That is more severe than the documented heuristic risk
of an indirect dependency being missed (`design/operations/pr-ci-fast-gate.md:37-43`).

Before merge, either make the integration manifest executable in an
appropriate path-selected lane or add lanes/selectors that cover every entry.
Also add a fast hard-lane invariant that every `src/tests/**/test_*.py` file is
present in exactly one of the unit or integration inventories, every manifest
entry exists, and every integration entry is reachable by at least one PR lane.

### P1.3 — Path-selection infrastructure fails open relative to `PR gate`

`integration-workers`, `integration-surfaces`, and `compose-quickstart` all
depend on `changes` (`.github/workflows/ci.yml:121-127`,
`.github/workflows/ci.yml:160-166`, `.github/workflows/ci.yml:199-204`). If the
selector action fails because of invalid filter YAML, permissions, or an action
failure, those jobs are skipped. `PR gate` depends only on `quality`, `unit`,
and `contract-smoke` (`.github/workflows/ci.yml:379-393`), so it can still pass.
Branch protection configured as designed would then allow a merge despite the
mechanism selecting all soft safety lanes being broken.

The configuration also does not self-test. Workflow and `.github/ci/**`
changes match the `quality` filter (`.github/ci/path-filters.yml:3-11`), but the
`quality`, `unit`, and `contract` outputs are unused
(`.github/workflows/ci.yml:26-33`). Those control-plane changes do not force the
worker, surface, or compose lanes. In particular, this CI-only change does not
exercise the new conditional lanes on its own PR.

Make `changes` a dependency of `PR gate` and require its result to be
`success`. Treat changes to `ci.yml`, `path-filters.yml`, and the test manifests
as broad-selector changes that exercise all affected conditional lanes, or add
deterministic selector tests with representative changed-path fixtures.

### P1.4 — The nightly mitigation has no alerting implementation or owner

The accepted merge risk relies on “nightly alerts on `main`”
(`design/operations/pr-ci-fast-gate.md:37-41`), but the nightly workflow ends at
the coverage action and defines no notification, issue creation, escalation,
or ownership step (`.github/workflows/ci-nightly.yml:101-119`). A red scheduled
run visible only in the Actions UI is not an operational alert. With soft
integration checks, unnoticed nightly failures can accumulate on `main`.

Before relaxing PR protection, define a monitored destination, responsible
owner, expected acknowledgement time, and recovery/escalation behavior for a
nightly failure. Verify the alert using a controlled failing dispatch.

## P2 findings

### P2.1 — The sub-ten-minute objective is asserted but not measured

The design targets less than ten minutes, while the hard jobs allow 15, 15,
and 20 minutes (`.github/workflows/ci.yml:41-44`,
`.github/workflows/ci.yml:65-68`, `.github/workflows/ci.yml:85-88`). Timeouts
are safety ceilings, not an SLO, but there is no job-duration summary, baseline,
or percentile acceptance criterion. Record cold-cache and warm-cache p50/p95
for the three hard lanes and the aggregate gate before claiming the target.

### P2.2 — New third-party execution remains tag-pinned rather than commit-pinned

The selector and coverage actions are referenced by mutable major tags
(`.github/workflows/ci.yml:36`, `.github/workflows/ci-nightly.yml:116`), as are
the setup actions used throughout both workflows. Pin third-party actions to
reviewed full commit SHAs and let Dependabot update them. This matters most for
`dorny/paths-filter`, because its result decides whether safety lanes run.

### P2.3 — The aggregator and nightly permissions are broader than necessary

`PR gate` declares no explicit permissions despite needing no repository token
access (`.github/workflows/ci.yml:379-393`), so it inherits the repository
default. Give it an empty or read-only permission set and a short timeout. The
scheduled coverage job requests `pull-requests: write` even though neither of
its triggers is a PR (`.github/workflows/ci-nightly.yml:6-10`,
`.github/workflows/ci-nightly.yml:101-107`); remove that permission unless a
documented action requirement demonstrates it is needed for these events.

### P2.4 — Nightly runs the migration test twice

The nightly matrix first runs `src/tests/spine/test_migrations.py` explicitly
and then runs all of `src/tests` (`.github/workflows/ci-nightly.yml:88-93`),
which includes the same file again. Keep the explicit lifecycle step only if it
has distinct setup or reporting value; otherwise remove the duplicate to save
matrix time and reduce stateful-test exposure.

## Branch-protection migration

Protection settings are external state and cannot be verified from this diff.
Use this sequence for `main`:

1. Confirm whether protection is implemented as required status checks or as a
   required workflow, and whether a merge queue is enabled. Record the current
   settings for rollback.
2. Let this PR produce one successful `PR gate` check. GitHub generally only
   offers a check as required after it has completed successfully in the
   repository recently. The PR may remain blocked by the old `Coverage report`
   while this happens.
3. In one administrative cutover, add the exact `PR gate` context (bound to the
   GitHub Actions app where supported), retain the separate `CLA` context, and
   remove `Coverage report` plus any old matrix contexts such as `checks
   (3.12)`, `checks (3.13)`, and `checks (3.14)`. Remove `Compose quickstart`
   too if it was previously required. Do not require `Path filters`, the three
   component hard jobs, either integration job, `Compose quickstart`, or any
   `Nightly CI` job.
4. If the repository currently requires the whole `CI` workflow, replace that
   rule with the exact `PR gate` status check; otherwise a deliberately soft
   integration failure can still block merging through the workflow result.
5. If a merge queue is enabled, do not cut over until `.github/workflows/ci.yml`
   also handles `merge_group` and a queued test PR has emitted `PR gate`. The
   current triggers cover only `push` and `pull_request`
   (`.github/workflows/ci.yml:7-10`).
6. Validate four cases before declaring migration complete: a docs-only PR
   emits and passes `PR gate`; a hard-lane failure fails it; a deliberately
   failing soft integration job leaves `PR gate` successful and merge
   available; and a selector failure fails `PR gate` after P1.3 is fixed.
7. After merge, confirm the `push` run on `main`, manually dispatch the nightly
   matrix, verify coverage publication, and test the alert route from P1.4.

If `PR gate` does not appear or behaves incorrectly, temporarily require all
three component contexts (`Quality`, `Unit`, and `Contract smoke`) together
with `CLA`, or restore the previous workflow and protection snapshot. Never
fall back to `CLA` alone.

GitHub's relevant behavior is documented in
[Troubleshooting required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks):
required contexts must be reported on the tested commit, skipped dependent jobs
need `always()` handling, and merge queues require a `merge_group` trigger.

## Follow-ups

Required before merge:

- Resolve P1.1 through P1.4 and update the design with the resulting invariant,
  ownership, and rollout details.
- Run one PR proving the selector routes for worker, surface, shared-core,
  self-host/compose, CI-control, and an integration test outside the four
  currently hard-coded test directories.
- Perform the branch-protection migration and attach evidence of the four
  validation cases to the change record.

After merge:

- Add the proposed pytest `unit` / `pg` / `graph` markers and generate manifests
  from collection metadata rather than a text-search heuristic.
- Track PR-gate p50/p95 duration and flakes for at least two weeks; assign an
  owner and threshold for revisiting the lane split.
- Add diff coverage only after the hard-gate and nightly alerting behavior is
  stable.
- Pin action SHAs, minimize token permissions, and remove the unused
  `quality` / `unit` / `contract` selector outputs.
