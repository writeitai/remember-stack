# Packaging & Distribution — Artifacts, Task Execution, Code Architecture (Design)

> **Binding D98 amendment (2026-08-27).** The shipped database is the pinned
> PostgreSQL 19 image with pgvector, pg_textsearch, pg_partman, and native
> SQL/PGQ. Packaging contains no Ladybug wheel, graph snapshot volume, graph
> builder/reader, or public Cypher API/CLI/MCP surface. Typed live graph
> operations and public bounded SQL helpers remain. Pre-D98 references to open
> Cypher or P2 packaging are removed requirements.

What the open-source library *ships as*, how work physically executes on both deployment
profiles, and how the codebase is structured so the substrate stays swappable without
spaghetti. Binding design for decision **D62**, filling in what D60 (the OSS boundary) and D61
(provider ports) left as the unwritten packaging design (`questions.md` §11a). Companion
analyses: the cloud repo's `analysis/oss_cloud_split/` (the Sentry-shaped split) and the
brainstorm rounds recorded in this repo's PR #37. Numbers and tool choices marked *(slot)*
await the owner-provided stack conventions (roadmap §3).

> **Reading this cold (CLAUDE.md Rule 1).** The system is a per-deployment memory pipeline:
> workers process documents in stages (E0 convert → E1 chunk → E2 extract → E3 adjudicate …),
> each stage recorded in the Postgres table `processing_state` (D12: one row per
> target/stage/version — pending/running/succeeded/failed/dead-letter, with normalized route,
> due-time, retry, and parking state under D67). **D61 ports** are
> narrow interfaces over the deployment substrate (object store, task queue, mounts, K git
> remote, model providers, telemetry, auth perimeter), each with a **self-host** adapter and a
> **GCP reference** adapter. Jargon used below: **at-least-once delivery** = a task may be
> handed to a worker more than once (never zero times), so handlers must be **idempotent**
> (safe to re-run — D12 already mandates this); **`LISTEN/NOTIFY`** = Postgres's built-in
> publish/subscribe — a session can sleep on `LISTEN channel` and be woken instantly by any
> transaction that runs `NOTIFY channel`; **`SELECT … FOR UPDATE SKIP LOCKED`** = the Postgres
> idiom that lets N workers each atomically claim different pending rows with no coordinator
> and no double-claims; **hexagonal / ports-and-adapters** = the code architecture where pure
> domain logic depends on interfaces ("ports") and vendor integrations live in swappable
> "adapters" at the edge; **extras** = optional pip dependency groups (`pip install
> pkg[server]`).

## 1. The delivery artifacts — three, each with a distinct consumer

| Artifact | Consumer | Contents |
|---|---|---|
| **The GitHub repository** | contributors, evaluators | source + the `plan/` design corpus (itself a differentiator: the architecture rationale ships with the code) |
| **The PyPI package** — dist/import **`rememberstack`**, CLI **`remember`** (product **RememberStack**, canonical home **`remember.dev`**; D76) | **agent harnesses and their operators** — positioned as *the client* | base install = the **client surface** (§2): typed SDK, CLI, MCP server. Extras: `[server]` (workers, spine, adapters), `[connectors-gdrive]` etc. (per-connector, server-side), `[k]` (the K compile machine's driver dependencies) |
| **One container image + compose profile** (published on **GHCR** — same org as the repo, no second registry account) | self-hosters, CI, benchmarks | one shared image runs `api`, `worker`, or one-shot `setup` commands; `docker compose up` brings up the **self-host profile**: Postgres + MinIO + api + worker(s). API and workers execute the same package and dependency set, so separate images would duplicate publication without creating an isolation boundary. The ten-minute quickstart is a release-gating, CI-tested artifact — an infrastructure-shaped OSS that cannot be *tried* quickly dies |

One package, not a package family: the same distribution contains client and server code;
extras select dependency weight. The *positioning* is what differs: the README sells
`pip install <pkg>` as "connect your agent to a memory deployment," because the designed
consumers are harnesses (requirements §Retrieval); operators install `[server]`.

## 2. The client surface (what the base package exposes)

- **Query**: the typed SDK + CLI + MCP server over the retrieval API (D48–D51, D87) —
  primitives, the four closed assured operations, D49 envelopes, open SQL with bounded live-graph helpers, and governed
  saved queries. MCP renders the four platform-owned assured descriptors plus open-query
  infrastructure; customer and `examples.*` saved queries do not become top-level intent tools.
- **Ingest — lineage-aware by contract**: `client.ingest(bytes|path, *, source_kind=…,
  source_ref=…, source_modified_at=…, versioning_mode=…)` / `remember ingest …`. Writes always
  enter through E0 (D60 invariant — no surface writes around the pipeline). The optional
  lineage fields are the load-bearing part: a caller that pushes the same logical document
  repeatedly with a stable `source_ref` creates **versions of one lineage** with full D54–D56
  lifecycle semantics (currency, reuse, retraction) — which is how third parties build
  *push-style feeders* for sources the deployment cannot poll (behind-firewall systems),
  without us shipping their connector. Omitted lineage fields = a one-shot upload lineage.
- **Connector management, never execution**: `remember connectors add|list|pause|status`.
  Connectors (Drive, mailboxes, URLs) execute **deployment-side** as workers — they own sync
  cycles, debounce, and deletion detection (`connector_sync_cycles`, D55), semantics that
  must not depend on a client process staying alive. The client configures them; credentials
  live deployment-side.
- **Admin**: the D24 review CLI (cluster review, `support_withdrawn` triage) and deployment
  introspection (pipeline state, DLQ, budgets) — reading state the spine already persists.

## 3. Task execution — one model, two delivery shells

**The unifying rule (this section's core, refining D61's queue-port row):**

> **Work is Postgres rows; the queue port only delivers wake-ups.** `processing_state` (D12)
> is the sole authority for what must run, is running, succeeded, failed, or dead-lettered.
> Under D67 it also owns the logical route (`deployment_id`, `stage`, nullable `lane`), earliest
> delivery time (`not_before`), defer reason, and application-attempt limit. The task queue never
> *owns* work — it *announces* an existing row. Consequently there is no push-vs-pull split in
> application code: both adapters implement "announce this `processing_id`."

For plane-E work, `lane` is `steady` or `backfill`; K/P jobs have `lane IS NULL` because their
debounce/schedule trigger models are not lanes. The logical queue identity is
`(deployment_id, stage, lane)`. Physical queue names are adapter configuration derived from that
tuple, never persisted work state. `lane` is deliberately absent from the D12 idempotency key:
the same target/stage/version discovered by a live ingest and a backfill is one unit of work, not
two competing rows. `not_before` is the only earliest-delivery term; `run_after` is not a second
field or API alias. Neither route nor due time may be hidden in `payload`.

**Application code is written once, as handlers.** Each stage registers
`handle_<stage>(task) → result`. A completing handler writes its results + its
`processing_state` transition + the successor's `processing_state` row (the chain rule,
orchestration §1) through one shell-agnostic scheduling service. That service persists through
`spine/`; the self-host insert trigger supplies the initial transactional wake, while the
reference profile calls the queue port after commit. Retry/replay/janitor paths call the port in
both profiles. The port never inserts `processing_state`. Handlers are idempotent
(D12; both adapters are at-least-once) and never know which shell invoked them. On delivery the
dispatcher re-reads and atomically claims the row; delivery-envelope fields and provider headers
are hints, never inputs to a state transition.

**The two shells (~200 lines each, in `adapters/`):**

- **Self-host shell — `LISTEN/NOTIFY` + `SKIP LOCKED` (not naive polling).** `spine/` inserts
  the task row. A schema-owned Postgres `AFTER INSERT` trigger performs
  `pg_notify('queue_wake', processing_id)` in that transaction; Postgres delivers it only after
  commit, so crash between "state committed" and "initial wake-up recorded" is impossible by
  construction. The self-host adapter's `announce` method never inserts: for retry, replay, or
  janitor re-announcement it invokes the injected `spine/` notification primitive for the
  existing row (SQL remains exclusively in `spine/`). Worker processes sleep
  on `LISTEN`; a notify wakes them in milliseconds; the woken worker claims only rows on its
  configured `(deployment, stage, lane)` route whose `not_before <= now()`, using
  `SELECT … FOR UPDATE SKIP LOCKED LIMIT n` and the schema §2 due-work index. A **slow fallback
  poll (~30 s)** exists only as a safety net for missed notifications, scheduled work becoming
  due, and crash recovery. Application attempts/backoff remain row state; per-route rate limits
  are a token bucket consulted around the claim. Scaling = `docker compose up --scale worker=N`.
- **GCP reference shell — Cloud Tasks push.** Announcement = create a Cloud Task; delivery = an
  HTTP push to a thin Cloud Run handler-dispatch server. The task carries `processing_id` plus a
  snapshot of route and `not_before`; Cloud Tasks scheduling and per-route rate limits reduce
  needless pushes, but the dispatcher still re-reads Postgres. Provider delivery attempts and
  headers are diagnostic only. An application failure updates `attempts`, `status`, and
  `not_before` in Postgres before the row is re-announced; an early or stale duplicate performs
  no work. Cloud Run autoscales on push.

**The janitor closes both shells' gaps with one mechanism.** A scheduled sweep re-announces
due `pending` or `failed` rows whose delivery evidently got lost. On GCP this repairs the
non-transactional announcement window (state committed, Cloud Task creation crashed); on
self-host it backstops lost notifications. Future-scheduled and budget-parked rows become eligible
only at `not_before`. The same code is port-agnostic because truth was in the row all along.

**The port contract** (what both shells must satisfy; the orchestration design's queues,
lanes, and budgets sit on top, adapter-agnostic) has one operation:
`announce(processing_id: UUID, route_snapshot: QueueRoute,
not_before_snapshot: UTC datetime)`, where `QueueRoute` is the typed snapshot
`{deployment_id: UUID, stage: pipeline_stage, lane: processing_lane | None}`. The snapshots let an
adapter pick the physical queue and delivery time; the row must already be committed and only
`processing_id` identifies work. The port refuses to create or mutate state, and the receiver must
validate the snapshots against Postgres. An early, stale, or mismatched delivery is
acknowledged without entering the handler; the current row is announced on its authoritative route
when due. The contract provides at-least-once delivery, per-route rate limits, and scheduled
announcement. **Application** retry/backoff, attempt limits,
budget parking, and the DLQ are `processing_state` transitions under D67; provider retry metadata
cannot override them. A third, **test-tier** adapter — in-process/synchronous — exists for unit
tests and local hacking; it is test infrastructure, not a maintained deployment adapter (D61's
two-adapter discipline governs what we *maintain*, not what the port permits — a community
Redis/arq adapter is possible against the same contract; considered and not chosen for maintained
self-host: it adds a second stateful service to every deployment and gives up transactional
enqueue, for throughput this LLM-bound pipeline never needs).

### Cross-source queue-state contract (D67)

This is the implementation map; each row has one canonical owner and a delivery-only projection:

| Concern | Canonical decision / orchestration rule | Normalized Postgres home | Port/adapter view |
|---|---|---|---|
| Work identity | D12: one target/stage/component version | `processing_state` unique key, excluding lane | `processing_id` identifies the row |
| Route | orchestration §2: deployment + stage + steady/backfill; K/P unlaned | `deployment_id`, `stage`, nullable `lane` | route snapshot chooses a physical queue; receiver validates it |
| Earliest delivery | D67: `not_before` is the only term | `processing_state.not_before` + typed `defer_reason` | schedule snapshot is a latency hint; early delivery no-ops |
| Retry / DLQ | orchestration §6: handler starts count; limit or non-retryable failure dead-letters | `status`, `attempts`, `max_attempts`, `last_error`; DLQ = `status='dead_letter'` | provider attempts/headers are diagnostic and cannot change state |
| Budget parking | orchestration §4: pending until window roll, no attempt consumed | `status='pending'`, `defer_reason='budget'`, future `not_before` | re-announce for that time; never a failure delivery |
| Cost attribution | orchestration §4: one row per processing/attempt/logical call; a batched call bills the claiming row; sum by deployment/stage/lane/window | `cost_ledger.processing_id/attempt/call_key/lane` + unique key + budget-window index | no adapter-owned accounting |
| Due claim | D67: only due pending/failed work is runnable | `ix_procstate_due` + schema §2 `SKIP LOCKED` query | self-host polls/claims; GCP dispatch claims its named row |

## 4. Code architecture — hexagonal, with the arrows enforced in CI

```
rememberstack/
  model/       # typed domain objects (Pydantic at boundaries; TypedDict/enum/Literal inside) — imports nothing
  core/        # PURE logic, zero I/O: blockizer, packer, snap algorithm, grounding checks,
               # counting rules, currency rules, envelope assembly — unit-testable with no infra
  spine/       # the Postgres access layer — the ONLY place SQL lives (repositories per aggregate)
  ports/       # seven D61 substrate Protocols + D74's narrow intent/purge capabilities
  adapters/
    selfhost/  # MinIO/S3 object store, the pg-queue shell (§3), local-dir mount publisher,
               # plain git remote, BYO model keys, OTLP telemetry, API-key auth
    gcp/       # GCS, the Cloud Tasks shell, gcsfuse publication, hosted repo, configured
               # providers, managed telemetry
    testing/   # in-process queue, tmpdir object store — the test tier
  llm/         # the programmatic-LLM layer (D52 class 2): prompt registry (rendered from the
               # ontology and closed assured-operation registries), schema-constrained calls, transcript writing —
               # every extractor/adjudicator call goes through here, no exceptions
  workers/     # stage handlers: thin orchestration composing core + spine + ports
  surfaces/    # api / mcp / cli — depend on services, never on adapters
  eval/        # the D22 harness, canaries, contract tests
  profiles/    # composition roots: selfhost.py / reference.py wire a deployment from config —
               # explicit constructor injection, no DI framework, no magic
```

**The anti-spaghetti mechanism is not the layout — it is the mechanically enforced dependency
direction** (the user's requirement "no spaghetti" as a failing test, not a convention):

- `core` imports only `model`. `workers` import `core` + `ports` + `spine` — never
  `adapters`. `surfaces` never import `adapters`. Vendor SDKs (`google.cloud.*`, `boto3`,
  `redis`, …) are importable **only** inside `adapters/`. SQL strings/builders exist **only**
  inside `spine/`.
- Enforced with **import-linter** contracts in CI: an illegal import is a build failure, not
  a review comment. Architecture erosion fails loudly (the project's standing value).
- Every worker is a thin shell over pure `core` functions, so the logic tests run without
  Postgres, queues, or GCS; integration tests run on the `testing/` + compose profile.

This is what makes "develop locally / deploy on GCP / build the cloud later" a **profile
selection**, not a refactor — and per D60 rule 2, the cloud product consumes exactly these
ports and published extension points, keeping it portable off GCP too.

## 5. Deployment profiles

- **Self-host profile** (the compose file, shipped + CI-tested): `postgres` (the spine + the
  queue + the DLQ — one stateful service), `minio` (object store), `api`, `worker` (×N),
  optional `k-driver`. Mounts publish to a local directory tree. Dev loop: `docker compose up
  postgres minio` + run api/worker from source; pure-logic work needs no containers at all
  (`adapters/testing`).
- **Reference adapters** (GCP — the substrate implementations the cloud consumes): Cloud Run/
  Cloud Tasks and GCS/gcsfuse adapters ship and are contract-tested here per D61. Production
  provisioning, Terraform, topology, scaling, HA, backups, and monitoring live in
  `ultimate-memory-cloud`, not in this library.
- Profile choice is configuration consumed by `profiles/`; no code path branches on "am I on
  GCP" outside adapters.

## 6. Releases, upgrades, portability

- **Versioning**: semantic versioning on the package and shared image (same version string);
  every release publishes PyPI + the GHCR image + the compose file pinned to that tag.
  *(D76: product RememberStack; dist/import/container `rememberstack`; CLI `remember`; canonical
  home `remember.dev`.)*
- **Upgrades**: Alembic migrations run **before** workers roll (the schema doc is the source
  of truth; migrations implement it). Processing-version stamps (D7/D12) mean code upgrades
  never silently invalidate derived state — reprocessing is explicit, per version filters,
  through the Phase-7 lanes.
- **Portability is a state-and-ordering contract, not a transport subsystem (D75)**: a
  deployment's portable state is exactly its **sources of truth** — the Postgres database,
  raw + artifact objects, the K git repository, and the separately durable D74 hard-forget
  manifest root. Operators move those stores with their native tools (`pg_dump`/`pg_restore`,
  provider object copy, and ordinary Git); the library does not wrap them in `remember export` /
  `remember import`, define a universal archive, or schedule backups. P1 and P3 are derived and are
  rebuilt after restore rather than transported; the live graph is PostgreSQL metadata/views and
  restores with the database. This is the cloud↔self-host migration
  contract in both directions without turning the OSS library into an operations product.

The portable restore order is deliberately small and fail-closed:

1. Quiesce writes and capture a consistent operator-managed snapshot of Postgres, objects, and
   the K repository while preserving the deployment id.
2. Transfer and verify the deployment's hard-forget manifest root **first**, through the
   separate durability channel required by D74.
3. Restore Postgres, raw/artifact objects, and the K repository with their native tools, then run
   normal schema migrations.
4. Run the ordinary hard-forget readiness pass before any serving surface opens. It rematerializes
   missing local intent and re-honors every manifest against every restored store.
5. Rebuild P1 and P3 through their normal production builders, ensure the property-graph catalog
   and bounded traversal helpers, run the S55/control canaries, and
   only then admit traffic.

Credentials, provider configuration, backup retention, transfer progress, retries, manifest-root
verification, and cross-store snapshot policy remain operator or `ultimate-memory-cloud`
responsibilities. Losing or omitting the manifest root makes a restore unsafe. An unavailable or
unprovisioned root fails readiness; a reachable empty replacement cannot disclose what was lost and
must be rejected by the operator's transfer verification.

## 7. Decision interactions

| Decision | Effect |
|---|---|
| D60 (OSS boundary) | *implements*: the complete-single-deployment state contract keeps self-hosting a first-class exit while byte transport and backup operations stay operator-owned |
| D61 (ports) | *refines the queue row*: queue = delivery-only announcements over `processing_state` truth; self-host adapter confirmed as the Postgres shell; test-tier adapter named |
| D12 (idempotency, DLQ) | *load-bearing*: at-least-once + idempotent handlers is the uniform execution contract; handler starts count attempts; DLQ stays Postgres rows on both shells |
| D67 (normalized queue state) | *owns the reconciliation*: nullable lane, `not_before`, defer reason, attempt/DLQ transitions, lane-attributed cost, and due-work claim index have one Postgres home |
| D50/D51 (trust, surfaces) | *unchanged*: API-key perimeter in the library; ingest is a client capability but writes through E0 |
| D54–D56 (lifecycle) | *extended to clients*: the lineage-aware ingest contract lets push-feeders participate in versioning |
| D52/D53 (execution classes, model split) | *housed*: the `llm/` layer is where class-2 calls and family assignments live |
| orchestration design | *shared semantics*: queue routes, lanes, budgets, retry, and DLQ transitions are defined against the same D67 state and both shells satisfy it |

## 8. Spikes / open slots

1. **Stack-convention slots** *(owner-provided; gate WP-0.1)*: package manager, lint/format,
   CI provider, secrets handling. The layout and import-linter contracts above are bound.
2. **pg-queue shell parameters**: fallback-poll interval, claim batch size, token-bucket
   granularity — measure under Phase 7's fixed portable scale profiles.
3. **Compose quickstart UX**: measure the cold-start-to-first-query time; it is a release
   gate (target: minutes).
4. **Portable restore round-trip drill — completed by WP-7.7**: retain the manifest root, restore
   the pre-forget PostgreSQL fixture and independently restore external stores, run readiness, then
   prove forgotten-data non-resurrection and an independent control green. Production-builder
   delegation remains the separately green WP-7.4/WP-7.5 contract. No library transport feature is
   involved.
5. **MCP server distribution**: whether the MCP server also ships as a standalone binary/uvx
   target for harnesses that don't want a Python env — decide with the first external users.

## References

Decisions: **D62** (this design), D7, D12, D48–D56, D60–D61, D76–D77 (`decisions.md`). Cloud-side
analyses: `ultimate-memory-cloud/analysis/oss_cloud_split/` (synthesis + licensing/naming).
Plans: `plan/plans/roadmap.md` §3 (stack), phases 0/5/7 (the WPs realizing this design).
Resolved governance record (name, bounded CLA): `questions.md` §11a.
