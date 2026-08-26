# Adversarial implementation review: PR 311, WP-I.2 hard type cut (round 3)

**Reviewer:** Claude Opus 5 (`claude-opus-5`, xhigh effort)  
**Date:** 2026-08-26  
**PR:** `writeitai/remember-stack#311`  
**Branch:** `origin/feat/wp-i2-type-cut` vs `origin/main`  
**HEAD reviewed:** `1e8510230799bc47e350d57f987aeb49d64e4160` (verified; clean)  
**Verdict:** **Request changes**

The reviewer read `CLAUDE.md`, D95–D97, the binding entity-identity design,
WP-I.2, the full diff, and all prior I.2 reviews. It deliberately did not ask
for backward compatibility, dual writers, mixed-generation drain, entity
types/hats, LoCoMo optimization, or `t0_exact_accept`.

## P1 findings

### P1-1 — Vacated public columns were still documented as live type data

The migration correctly made these compatibility positions always NULL:

- `memory_v1.entities_current.entity_type`
- `memory_v1.entities_current.type_confidence`
- `memory_v1.mentions_live.emitted_type`
- `memory_v1.mentions_live.type_confidence`

However, PostgreSQL and the checked-in manifest still described them as a
canonical type vote and extractor-emitted type. Raw SQL and saved-query callers
could follow that contract, filter on an always-NULL column, and report a false
empty result. This re-opened the documentation half of Codex r1 P1-4.

Required remedy: author and materialize explicit vacated meanings for all four
public columns, regenerate the manifest, and repin the manifest/protocol hashes.

### P1-2 — Shipped docs and a worked example still used `entity_type`

The implementation removed `entity_type` from `resolve_entity` and `/resolve`,
but the getting-started and API curl examples still passed it. Because unknown
operation arguments are rejected, those copied commands failed. The API
descriptor example, operation table, primitive table, retrieval page, and the
ingestion page's D86 prose were also stale. The consumption skill's
snapshot-to-live SQL selected a permanently-NULL `entity_type` column.

Required remedy: remove `entity_type` from all shipped resolve documentation,
remove the obsolete D86/signature-gate prose, and make the worked SQL select
useful untyped data.

### P1-3 — `postgres_schema_design.md` still specified the pre-D96 schema

The binding schema design still contained `entities.type`,
`entities.type_confidence`, mention type columns, `predicate_signatures`, its
domain/range enforcement contract, the typed entity index/FK, and a typed
decision-map entry. The binding entity-identity design explicitly required the
migration PR to amend this document.

Required remedy: make the binding schema describe the accepted D95–D97
architecture and the actual hard cut.

## P2 observations

1. `typed_absence` retained an ignored `entity_type` argument and stale
   ontology docstring after its SQL filter was removed.
2. `entity_type` scope interests, extension-pack signatures, and per-type
   threshold configuration survived as silently inert inputs.
3. `UnregisteredEntityTypeError`, `_TYPE_RETRY_SUFFIX`, `P1EntityRow.type`,
   and the P1 search `entity_type` parameter remained as dead D18/D86 surface.
4. The D86 test module was permanently skipped and its only test would
   unconditionally fail if enabled; other tests imported it as a fixture file.
5. `e0_files_design.md` still showed `entities/<type>/<entity_id>/` in its body.
6. Downgrade restored nullable columns but not the old entity-type index or
   table comment, so its schema was not fully symmetric.

## Verified closed from earlier rounds

The reviewer independently confirmed:

- all view dependencies are replaced before authority-column drops;
- no live `predicate_signatures` reader/writer remains;
- `EntityRef`, resolver SQL, mint SQL, API/SDK, P1/P2/P3, and `GraphNode` are
  untyped;
- the downgrade provenance condition is present;
- catalog counts and object inventories are internally correct;
- hard forget no longer updates a removed column;
- `works_for` persists without a domain/range gate;
- P3 path/link depth is correct;
- the bad global literal rewrite was repaired. A custom AST audit examined 431
  `INSERT ... VALUES` tuples and found zero arity mismatches.

## Checks reported

- `git rev-parse HEAD` matched the required SHA; worktree was clean.
- `uv run pyright src/ benchmarks/` — 0 errors.
- `uv run ruff check src/ benchmarks/` — passed.
- `uv run pytest -q src/tests` — 1263 passed, 643 skipped, 19 database-fixture
  errors because no local `REMEMBERSTACK_DATABASE_URL` was available.
- Static view-dependency enumeration, manifest-comment extraction, constraint
  derivation, and INSERT-arity analysis were performed.
- Database execution was deferred to the already-green GitHub Actions lanes.

## Verdict

**Request changes.** The three P1 findings had to close before merge.
