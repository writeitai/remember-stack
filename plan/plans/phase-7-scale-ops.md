# Phase 7 — Operational Correctness + Portability

> The filename is retained as a stable historical path; this title and D60 define the current
> scope. "Scale + Ops" is no longer the phase contract.

**Goal:** finish the mechanisms a complete single-deployment OSS library must own: resumable
backfill, reproducible scale checks, enforced configurable budgets, visible failures, portable
deletion, releases, and a restore-safe portability contract.

**Scope boundary (D60):** this phase ships library behavior, adapter contracts, self-host
surfaces, and deterministic drills. It does **not** choose or operate a hosted corpus capacity,
monthly spend policy, Postgres HA topology, observability backend, backup schedule, fleet, or
vendor-specific network layout. Those are `ultimate-memory-cloud`/operator concerns. The
reference adapters remain supported without turning their production operations into OSS logic.

**Simplicity rule (`implementation_core_values.md` §3):** add no control plane, dashboard service,
HA manager, backup scheduler, or parallel source of operational truth. Extend the existing
Postgres state, narrow ports, typed settings, and CLI/admin surfaces along one shared path for
self-host and reference adapters.

**Entry gates:** none. Work-package-local gates remain explicit: #24 was resolved by D74 before
WP-7.5 implementation. WP-7.6 engineering, account-level registry setup, and D77 governance
activation are complete. The public `v0.1.0` tag supplied the artifact proof.
**Exit criteria:** fixed synthetic scale profiles exercise the D23 shapes and portable batching;
a fixture budget parks and later resumes work without loss; telemetry/admin surfaces expose
pipeline and DLQ state; rebuild and forget drills pass; S55 is green across active serving stores
and a restore cannot resurrect a forgotten identity; release artifacts pass their round trips;
the operator-driven portable restore drill is green without a library transport subsystem.

**Exit evidence (2026-07-23):** WP-7.1–WP-7.7 are merged in PRs
[#120](https://github.com/writeitai/remember-stack/pull/120),
[#122](https://github.com/writeitai/remember-stack/pull/122),
[#123](https://github.com/writeitai/remember-stack/pull/123),
[#124](https://github.com/writeitai/remember-stack/pull/124),
[#126](https://github.com/writeitai/remember-stack/pull/126),
[#128](https://github.com/writeitai/remember-stack/pull/128), and
[#131](https://github.com/writeitai/remember-stack/pull/131). The
[`v0.1.0` release run](https://github.com/writeitai/remember-stack/actions/runs/30009101734)
passed its architecture, style, type, full-suite, and build steps plus its PyPI, GHCR, and
GitHub Release jobs;
the published GHCR manifest is anonymously readable.

| WP | Goal | Reads | Depends | Deliverable | Acceptance | Status |
|---|---|---|---|---|---|---|
| WP-7.1 | Backfill lanes + seeding + reprocessing orchestration (version bumps) | orchestration §3–4 | Phase 6 | lane machinery | steady-state unaffected during backfill test | done |
| WP-7.2 | Reproducible scale battery: D23 partitions/indexes, hub entities/lineages, recount cost, and provider-neutral read/write batching | schema §12; D23; lifecycle §11.5; orchestration §5; retrieval §13.7 | WP-7.1 | fixed synthetic profiles + report | shapes and batching invariants recorded; timings remain measurements, not hosted SLAs | done |
| WP-7.3 | Cost metering + configurable budget enforcement | orchestration §4; schema §2 `cost_ledger` | WP-7.1 | enforcement + admin inspection | explicit fixture ceiling parks and resumes an over-budget lane; attribution is visible | done |
| WP-7.4 | Operational correctness surfaces + drills: typed telemetry, pipeline/DLQ inspection and replay, P2/P3 rebuild, currency-ledger audit | orchestration §6–7; D7, D60–D61 | WP-7.1 | telemetry/admin surfaces + deterministic drills | failures remain visible and drills pass without a dashboard or hosted control plane | done |
| WP-7.5 | **Hard-delete end-to-end**: purge active P1/P2/P3/K surfaces and prevent restore resurrection through the D74 portable manifest/adapter contract | hard-forget design; lifecycle §8; k_layers §10; S55 | D74 (gate #24 resolved) | forget pipeline | **S55 CI gate ON and green** across library-controlled surfaces + restore canary | done |
| WP-7.6 | **Release engineering**: semver across PyPI + the shared GHCR image + pinned compose; migrations-before-workers upgrade drill; quickstart cold-start release gate | packaging §1, §5–6; D62, D76–D77 | WP-7.1, owner release gates | release pipeline | tagged release produces all artifacts; upgrade drill green; quickstart under target | done |
| WP-7.7 | **Portable state + restore round-trip**: define the authoritative store set and fail-closed restore order; operators move bytes with native tools and projections rebuild normally | packaging §6; D7, D60, D74–D75 | WP-7.1, WP-7.5 | portability contract + deterministic drill | real PostgreSQL restore plus whole/independent external-store canaries → no resurrection + control green | done |

## WP-7.1 implementation

`BackfillSeeder` enumerates the authoritative `processing_state` ledger for prior executions of
one plane-E stage that do not yet have the requested component version. Each configured-size
transaction copies the latest immutable target input into the existing D12 enqueue path on the
`backfill` lane. Re-running the same request is the cursor and recovery mechanism; no campaign
table, scheduler, or second pipeline exists.

Initial corpus loads use that same pipeline by selecting `backfill` at the upload boundary or via
the typed `REMEMBERSTACK_SYNC_LANE` setting. Steady work keeps its distinct claim route and still promotes
a pending/failed duplicate under the D67 rule. After seeding has completed and all deployment
backfill rows are terminal, PostgreSQL already contains the committed P1 search
state. There is no separate finalizer or search-store index build.

## WP-7.2 implementation

The `operational` eval suite records one complete four-part fixed-profile report in the existing
`eval_runs` history. Its capability-sized CI profile verifies the exact seven RANGE plus two
HASH-64 D23 parents, unpartitioned registry blocking targets and their GIN/Daitch-Mokotoff/btree
indexes (including measured index sizes), a 2,000-alias entity hub, and a lineage fanning out to
1,000 relations plus 1,000 observations. The same ungated shape scales through typed
`REMEMBERSTACK_OPERATIONAL_SCALE_*` settings; no corpus forecast or hosted target is embedded in the
library.

Lifecycle currency application and relation/observation recount are set-based: each remains one
transaction and a constant two SQL statements independent of batch size, while preserving retry
idempotency, last-transition cache semantics, distinct-current-lineage counts, and caller input
order. E3 front-loads normalized-claim replay markers and groups proposed observations by the
resolved `(document, entity)` batch. The adjudicator takes one entity lock, one claim-timestamp
read, and one exhaustive block read, then applies assertions in order against an in-memory block
that tracks new rows, caps, and contradiction groups before committing once.

The provider-neutral measurement uses the real SQLAlchemy engine with explicit injected latency:
513 interactive claim ids cross the binding 256-id boundary in exactly three confirmation
statements, while currency writes and hub recount retain their constant statement counts. Only
shape, correctness, query-count, and transaction-count invariants gate acceptance; every elapsed
time is recorded as a machine-specific measurement, never an OSS SLA or topology commitment.

## WP-7.3 implementation

Operators declare no implicit monetary policy. An optional typed `REMEMBERSTACK_WORK_BUDGETS` list supplies
explicit ceilings keyed by deployment, stage, lane, and aligned fixed window; an omitted route is
unlimited. After locking one due row, the existing claim transaction sums the deduplicated
`cost_ledger` range for that route. Exhaustion moves the row to durable `pending` / `budget` state
until the window boundary without starting a handler, consuming an attempt, changing the last
error, or creating a second scheduling ledger. The worker re-announces that existing row through
the delivery port with the stored resume time.

`WorkLedger.budget_status` and `remember budget inspect` read the same two authoritative Postgres
tables. They expose configured ceiling, current-window spend, remaining amount, tier attribution,
aligned bounds, and parked-work count; they do not add a dashboard, hosted billing policy, cache,
or control plane. The PostgreSQL acceptance fixture records two attributed calls, proves an
over-budget handler never starts, then crosses the fixture window and proves the exact row resumes
and completes normally.

Successful generation and embedding responses carry mandatory provider-reported usage through the
existing model port. The worker binds that usage to its running processing row, and every
model-using stage records a deterministic logical call key and cascade tier before consuming the
response. OpenRouter responses without cost/token accounting fail visibly instead of degrading to
zero spend; the deterministic test provider emits zero-cost usage so end-to-end worker tests prove
the same production attribution path without network calls.

## WP-7.4 implementation

`OperationalCatalog.inspect` reads one repeatable PostgreSQL snapshot and emits a typed,
deployment-scoped report. Route/status counts and the two latest projection pointers are bounded by
their closed vocabularies. Every variable diagnostic reports its complete total plus a sample capped
by the one typed `REMEMBERSTACK_OPERATIONAL_SAMPLE_LIMIT` setting: DLQ rows, DLQ stage/error/version groups,
poison targets, the component versions observed for each sampled poison target, and currency-ledger
mismatches. Error class is derived once from the last non-empty traceback line; complete tracebacks
and payloads remain available on sampled rows.

`WorkLedger.replay_dead_letter` owns the only replay mutation. It locks one deployment-owned DLQ
row, refuses any other status, preserves consumed attempts and `last_error`, grants an explicit
additional attempt allowance, validates an optional lane against the immutable stage, and returns
the authoritative committed route and due time. A thin service announces that result through the
ordinary queue port after commit; there is no bulk replay controller or second work ledger.

The existing worker exception boundary emits one provider-neutral `worker.run` event after each
committed success, failure, or budget park and emits nothing for `NO_WORK`. Exception export receives
the original exception object and exporter failures propagate. Self-hosting can write one JSON line
per event with the full exception cause chain; tests use an in-memory recorder. `remember ops inspect`,
`replay`, and `rebuild` are thin local admin commands. Rebuild selects the existing production
`GraphRebuildWorker` or `CorpusFsBuilder`, so the drill cannot diverge into a recovery-only path.

## WP-7.5 design gate (D74)

Hard-forget is one lineage-scoped, append-first workflow, not an extension of reversible normal
deletion and not a hosted operations feature. A content-free portable manifest is durably appended
outside the ordinary restore set before acceptance; PostgreSQL materializes that one intent and the
existing work ledger tracks execution. A durable `preparing` row blocks public/ordinary work while
leaving the authorized forget coordinator's internal calls available; pre-append failures are
resumed or safely reopen admission before any append attempt. The worker reuses the normal currency
cascade, scrubs PostgreSQL including its co-located P1 state, purges objects,
publishes clean P2/P3 snapshots and deletes old ones, then erases affected K paths
from history. Every serving readiness pass re-honors every portable
manifest—including locally complete ones—so independently restoring an old external store cannot
resurrect forgotten content.

Authored pages and compiled-page curation sidecars must be owner-redacted before acceptance. This
keeps D46 intact without introducing a human-only gate: the accountable owner may be an agent, but
the library never invents replacement authored prose. The normative record, ordering, adapter
hooks, scope boundaries, and S55/restore canary are in `plan/designs/hard_forget_design.md`.

## WP-7.6 implementation

`project.version` is the single release version. A small standard-library contract check requires
`vMAJOR.MINOR.PATCH`, the exact `v<project.version>` tag, and the same
`ghcr.io/writeitai/remember-stack:<version>` pin in Compose. The shared image runs setup, API, or
worker commands from one package and dependency set; separate images would duplicate publication
without creating a process-isolation boundary.

The tag-triggered release workflow runs architecture, style, type, full-test, and distribution
build gates before publishing. PyPI uses its short-lived OpenID Connect Trusted Publisher rather
than a stored token; GHCR receives the same semantic version without an unversioned `latest` tag.
Only after both registries accept their artifact does the workflow create the GitHub release with
the wheel, source distribution, pinned Compose file, and example environment.

CI measures a source-built cold start through the first API query against the binding 600-second
target. The upgrade drill starts from the immediately prior migration, preserves a bootstrapped
deployment, and holds the real one-shot setup process before migration. While held, CI proves the
schema is still old and the API plus both workers remain stopped. Releasing setup runs ordinary
Alembic/bootstrap code; only its successful completion permits those application processes to
start. The final checks require the head migration, preserved deployment, all three processes, and
a reachable health endpoint.

[`RELEASING.md`](../../RELEASING.md) records the deliberately manual owner steps and the clean
verification commands. The repository rename, D77 risk acceptance, PyPI environment/pending
publisher, release-tag protection, and bounded-CLA enforcement are complete. The first tag now
supplies the artifact proof, and the versioned GHCR package is public for anonymous pulls.

## WP-7.7 implementation

Portability is a contract over the existing sources of truth, not a new archive format or CLI.
The operator transfers Postgres, raw/artifact objects, and the K repository with their native
tools while carrying the separately durable D74 manifest root first. The deployment id is
preserved; migrations and the ordinary hard-forget readiness pass run before serving; P1/P2/P3
are rebuilt through their production paths. Backup schedules, consistency policy, credentials,
progress reporting, retries, and provider-specific transfer mechanics remain operator/cloud scope.

The deterministic drill reuses the WP-7.5 machinery rather than adding a backup engine.
`test_forget_catalog.py` restores the fixed pre-forget PostgreSQL fixture after local completion,
proves the local barrier row is absent, then exercises the readiness coordinator and portable
rematerialization against real SQL while preserving independent control evidence.
`test_s55_hard_forget.py` restores the whole logical serving state and each channel independently;
`test_s55_selfhost_restore.py` performs the external-store proof over real LocalFS
object/manifest stores, projection caches, and Git history. PostgreSQL hard-forget
tests cover the co-located P1 state. WP-7.4/WP-7.5 separately prove
that the forget rebuilder delegates to the production P2/P3 builders; that is an existing
dependency, not claimed as one composed restore test. Together the drills fail if portable intent
is omitted, readiness trusts a local completion bit, restored PostgreSQL or an external store
retains forgotten content, or unrelated memory is damaged. Preserving the deployment id and
verifying the transferred manifest root are explicit operator obligations, not drill-proven
identity conversion or loss detection.
