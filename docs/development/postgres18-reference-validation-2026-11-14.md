# PostgreSQL 18 reference-server validation and managed handoff

Owner decision: 2026-11-14, [D157](../../decisions.md#d157-postgresql-18-is-the-reference-server).
This record concerns the OSS engine change. It does not authorize or claim a
managed deployment, data transfer, release, or production capture qualification.

## Selected build

Official `postgres:18.6-trixie` index
`sha256:979bccfde35445db618c65107d27d08c6c21339d8dc3ad4c830c6633947be0e4`,
recorded by docker-library on 2026-10-28; server `18.6-1.pgdg13+2`.
18.7 was published 2026-11-12 and fails the required two-week age floor.
Base child digests and unchanged source/license pins are in
[docker/README.md](../../docker/README.md).

## Behaviour and contract differences

- The server-major checks in graph readiness, `memory_v1` and the native
  relation guard now accept only 18. PostgreSQL 18 matches the `pglast` 8.4
  parser's grammar; no parser bypass or second server path is added.
- PG18 ships `btree_gist` **1.8**, whereas the previous PG19 image shipped
  **1.9**. The native guard's complete extension set now pins 1.8. The
  remaining installed versions are unchanged: fuzzystrmatch 1.2,
  pg_partman 5.5.0, pg_textsearch 1.5.1, pg_trgm 1.6, pgcrypto 1.4,
  plpgsql 1.0, unaccent 1.1 and vector 0.8.6.
- PG18 advisory-lock statement timeouts have SQLSTATE `57014` and no error
  context. The old profile refresher required an `advisory lock` phrase and
  therefore missed typed contention on 18. It now identifies the two fixed
  lock statements plus SQLSTATE; unrelated statement timeouts remain database
  failures. The wait bound, retry contract and paid-work reuse inputs remain
  unchanged. Existing shared/exclusive contention and non-lock timeout proofs
  verify the distinction.
- pg_textsearch 1.5.1 compiles without the RC-only `-include limits.h` flag.
  Its archive, LICENSE and NOTICE pins are unchanged.
- PostgreSQL 18 already has `pg_constraint.contype = 'n'` for NOT NULL.
  Catalog counts remain subject to the full fresh/downgrade/re-upgrade gate.
- The obsolete Beta 3 `DROP PROPERTY GRAPH` helper and its migration/repair
  calls are removed. Revision `p9_44_0065` remains an explicit no-op to keep
  Alembic ordering. Existing relational views and bounded helpers are retained.
- Declaring server major 18 changes the public query-space manifest hash to
  `280cac8902f50aff8e6a02fe0fa39abdae6a64520e6a0b792bb8eef9c63e23e2`.
  The checked-in manifest, LoCoMo expected surface and D143 default-surface
  fixture are refreshed. Storage-capture synthetic source/schema/profile
  digests are regenerated. Historical receipts are not rewritten.
- The legacy D153 semantic oracle triggers just-in-time (JIT) compilation of thousands of
  functions on PG18 even for its tiny corpus. A read-only probe of old
  `pages_live` compiled 3258 functions and took 9.898 s; with JIT off it took
  0.209 s and returned identical rows. Only this row-equivalence test disables
  JIT locally on both comparison transactions; every assertion remains.
  Production settings and scale/plan gates are unchanged.
- No ingestion text, prompt, chunker, extraction, embedding or reuse identity
  generation changes. This server change does not require paid reprocessing.

## Validation

The shared disposable PostgreSQL container was recreated from the new arm64
image at `localhost:55433`, with database/user `rememberstack` and the
requested SQLAlchemy URL. Real MinIO was started by
`.github/ci/start-minio.sh` at `127.0.0.1:59000`.

Completed checks:

- PostgreSQL image builds on native arm64 and emulated amd64: upstream
  pg_textsearch **82/82 on each**, no forced header include. Server and
  pgvector/pg_partman package pins match on both. Local image/artifact hashes
  are in `docker/README.md`; CI/release use native runners for both platforms.
- Migration suite: **19 passed**, including fresh head, complete
  downgrade/re-upgrade, mutation/no-op and graph metadata lifecycle checks.
  The existing catalog constraint expectations passed unchanged.
- Ruff lint and formatting (`src/`, `benchmarks/`); Pyright with Python 3.13
  (**0 errors**); import contracts (**5 kept, 0 broken**); test inventory
  (**180 unit + 97 integration = 277 files**); release contract.
- Generated query-space manifest, D143 default surfaces, native capture
  fixture, OpenAPI (41 routes), TypeScript contracts/parity and public llms
  files. Public/cloud docs truth checks; public website production build and
  typecheck.
- All **40 converter checks** in the engine image with real LibreOffice,
  including the four tests skipped on this macOS host. Test files were
  copied into a disposable container; no converter implementation changed.
- Local Compose: healthy startup on PG18, loopback API at `18018`, exact
  stamped source revision, first SQL query, all three object buckets'
  round trips and conditional creation, zero-cost whitespace ingestion
  (eight completed stages and no cost rows), projection/readiness, and an
  empty prior-schema upgrade from `p9_31_0052` to `p9_53_0074` proving setup
  finishes before API/workers restart. The CI shell payloads were reused;
  the port was adjusted and the reset helper was copied rather than bind
  mounted because this Colima VM does not share the worktree's `/private/tmp`
  path. The Compose deployment and its volumes were removed after the checks.

The initial no-DB unit pack completed with **3585 passed**, 11 skips and
three stale-expectation failures: the old PostgreSQL-18 prose ban, the LoCoMo
surface hash and the D143 manifest hashes. All were corrected; the focused
rerun passed **65/65**. The first full PG18/MinIO run was stopped in the legacy oracle after
2659 passes and four LibreOffice skips (1043.91 s). The measured compilation
cost above justified the test-only JIT setting. Its focused complete parity
oracle passed in 91.58 s (83.80 s in the assertion loop).

The next complete 5199-case pass finished in 2290.71 s: **5175 passed,
2 failed, 20 skipped, 2 xfailed**. Its only failures were the two real
profile-lock timeout cases described above. The fixed complete profile
refresher module now passes **14/14**, including shared and exclusive lock
contention and the unrelated timeout negative proof. The final complete run
is repeated on that corrected runtime. The opt-in actual-LOGIN native capture
suites passed in separate empty scratch databases on the same PG18 container:
**9 guard tests** and **5 original-view tests** (5.52 s and 8.33 s). Both scratch
databases were removed after their tests.

The 20 skips comprise four host LibreOffice tests (covered by the container
run above), 14 explicit-scratch native capture tests (all passed separately), and two
fixture-conditional query tests where this corpus has no fact ID shared by
both relation and observation kinds. The two expected failures are the
existing D122 Claimify cross-section window gaps, in normal ingestion and
claims backfill; this change leaves them explicit and unchanged.

## Required remember.dev changes

Read-only audit of `writeitai/ultimate-memory-cloud` local checkout
`1f80bb577` on 2026-11-14. These are changes for a separate managed migration
PR and operational plan; no files in that repository or running hosts were
changed here. Replace historical source pins only when a release from this
engine change is available; this draft supplies no published foundation digest.

1. **Foundation and `umc-pg-01`.** Replace the PG19 foundation digest in
   `infra/opentofu/umc-postgres/prod.tfvars` and the current database inventory
   in `infra/prod.yaml` with the release's `remember-stack-postgres:18-<version>`
   manifest digest (amd64 child must be qualified). Change the recorded server
   version to the selected 18.x minor. Rebuild `umc-postgres:pgbackrest` from
   that digest with `infra/opentofu/umc-postgres/backup/Containerfile`; the host's
   `/opt/umc-postgres/compose.yaml` and `x-umc-base-image` must name that base.
   Preserve the TLS/HBA, PgBouncer, runtime/admin/collector roles and bounded
   settings. `cloud-init.yaml.tftpl` currently preloads `pg_textsearch` only;
   keep scheduled per-database partman maintenance unless explicitly selecting
   and configuring `pg_partman_bgw`.
2. **Storage and backup host paths.** Provision a fresh PG18 volume or empty
   host data directory; the major-specific path is `/var/lib/postgresql/18/docker`, under
   the existing `/var/lib/postgresql` volume mount. Change
   `backup/pgbackrest.conf`'s `pg1-path` from `/19/docker` to `/18/docker` and
   install the rendered config on the host. Plan a new qualified PG18 backup
   namespace (pgBackRest stanza/history) and full baseline with a restore drill; old PG19 physical
   backups/WAL are not PG18 recovery inputs. Do not open the PG19 directory
   on 18 or attempt a `pg_upgrade` downgrade. Inventory databases first and
   choose source replay or independently tested logical transfer if anything
   must survive. Coordinate the application stop/cutover with that choice.
3. **Capture schema/profile.** In
   `infra/capture/native/schema-definition.json`, change `postgres_major` from
   19 to 18 and `btree_gist=1.9` to `btree_gist=1.8`; bind the actual released
   schema head, complete root set and source revision. The engine-side guard
   now requires 18. `scripts/generate_capture_profile.py` currently carries
   `postgres_major` forward from the old schema document: update that
   generator to derive/assert 18 from the pinned engine contract, so it cannot
   regenerate an apparently current PG19 profile. Run the canonical
   `scripts/prepare_engine_pin_update.py` workflow (after the item 4 tag-validator
   change) to move the released engine
   pins/locks, then regenerate `infra/capture/native/` preimages, index,
   `runtime-source.json` and `run-profile.json`; run its offline check. Install
   guard/catalog functions with the existing least-privilege roles in every
   database, qualify the new image/source/schema and original native view,
   and register the new exact profile digests before resuming capture. Keep
   old dated receipts as history. Recheck clock admission on the actual host;
   synthetic engine fixtures do not qualify production clocks or billing.
4. **Runtime CI, pin automation and packaged engine.** First change
   `scripts/prepare_engine_pin_update.py`'s PostgreSQL receipt regex: it
   currently accepts only `19`, `19beta…` or `19rc…` tags and will reject
   `18-<version>`. Require the new `18` tag family and update its test receipts.
   Run it for a new released engine version; it deliberately refuses different
   immutable evidence for the same version, so do not repin released 0.20.0.
   It must update `infra/fleet-worker/engine-pin.json`'s foundation tag/major,
   manifest and platform digests, application/source/schema pins and
   `release/image-lock.yaml`. Change `.github/workflows/runtime-check.yml`
   from its digest-pinned `19rc1-0.18.1` foundation to the released PG18 digest.
   Advance `runtime/engine.lock`, `runtime/pyproject.toml`, the root lock and
   the engine source pin through the canonical pin update command. Run the
   runtime/provisioning suites against PG18, including native capture/profile
   freshness, actual LOGIN privilege/timeout tests, query-space and readiness.
   Rebuild runtime and provisioning images from those pins.
5. **Provisioning and dependent Compose.** Update
   `infra/fleet-worker/remember-stack-compose.yaml` to the release's PG18
   foundation and matching engine. Apply the accepted image/profile inputs to
   `infra/opentofu/umc-provisioning/` and `infra/opentofu/umc-business-pool/`
   (`main.tf` / `prod.tfvars` load the capture run profile) and their jobs. Verify an empty
   deployment creates all extensions and reaches the released Alembic head,
   runtime/admin/graph/query/capture roles retain the required grants,
   PgBouncer/TLS connections and per-database partman maintenance work,
   capture registration binds the new digests, and health/readiness/first
   ingestion succeed before routing traffic. Update the current host and
   provisioning docs/inventory (including the `umc-business-pool` reference)
   after readback; keep historical benchmarks and rollout receipts dated.
