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
- No ingestion text, prompt, chunker, extraction, embedding or reuse identity
  generation changes. This server change does not require paid reprocessing.

## Validation

Results are updated after the complete sequential database run below.
The shared disposable PostgreSQL container was recreated from the new arm64
image at `localhost:55433`, with database/user `rememberstack` and the
requested SQLAlchemy URL. Real MinIO is started by
`.github/ci/start-minio.sh` at `127.0.0.1:59000`.

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
2. **Storage and backup host paths.** Provision a fresh PG18 data directory or
   volume; the major-specific path is `/var/lib/postgresql/18/docker`, under
   the existing `/var/lib/postgresql` volume mount. Change
   `backup/pgbackrest.conf`'s `pg1-path` from `/19/docker` to `/18/docker` and
   install the rendered config on the host. Plan a new qualified PG18 backup
   stanza/history and full baseline with a restore drill; old PG19 physical
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
   `scripts/prepare_engine_pin_update.py` workflow to move the released engine
   pins/locks, then regenerate `infra/capture/native/` preimages, index,
   `runtime-source.json` and `run-profile.json`; run its offline check. Install
   guard/catalog functions with the existing least-privilege roles in every
   database, qualify the new image/source/schema and original native view,
   and register the new exact profile digests before resuming capture. Keep
   old dated receipts as history. Recheck clock admission on the actual host;
   synthetic engine fixtures do not qualify production clocks or billing.
4. **Runtime CI and packaged engine.** Change `.github/workflows/runtime-check.yml`
   from its digest-pinned `19rc1-0.18.1` foundation to the released PG18 digest.
   Advance `runtime/engine.lock`, `runtime/pyproject.toml`, the root lock and
   the engine source pin through the canonical pin update command. Run the
   runtime/provisioning suites against PG18, including native capture/profile
   freshness, actual LOGIN privilege/timeout tests, query-space and readiness.
   Rebuild runtime and provisioning images from those pins.
5. **Provisioning and dependent Compose.** Update
   `infra/fleet-worker/remember-stack-compose.yaml` to the release's PG18
   foundation and matching engine. Apply the accepted image/profile inputs to
   the personal/business provisioning roots and jobs. Verify an empty
   deployment creates all extensions and reaches the released Alembic head,
   runtime/admin/graph/query/capture roles retain the required grants,
   PgBouncer/TLS connections and per-database partman maintenance work,
   capture registration binds the new digests, and health/readiness/first
   ingestion succeed before routing traffic. Update the current host and
   provisioning docs/inventory (including the `umc-business-pool` reference)
   after readback; keep historical benchmarks and rollout receipts dated.
