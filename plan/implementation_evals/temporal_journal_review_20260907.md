# T.1 journal review — 2026-09-07

Status: scoped review and follow-up evidence for draft PR #384. This does not
approve full T.1, authorize release, or replace the remaining implementation
listed in `temporal_fact_t1_progress_20260907.md`.

Antigravity reviewed commit `223842d18a0bdd0f11cfac09abfa9e8b3904dbd7` using
`agy --dangerously-skip-permissions --print-timeout 180m0s -p ...` and returned
**changes requested**. The local complete output is
`/tmp/rs-t1-journal-antigravity-r1.log`. Its findings were checked against the
actual migration and implementation rather than accepted without verification.

## Finding dispositions

1. **Multiple generation rows — rejected as based on the wrong schema.**
   The review described a primary key `(deployment_id, generation)`. The frozen
   expansion migration actually defines `temporal_fact_generations.deployment_id`
   as its sole primary key, matching the binding SQL. There is one current
   certificate per deployment, not a generation history in this table. The
   admission query's `scalar_one_or_none()` has the intended cardinality. A
   new PostgreSQL test attempts a second row with another generation and
   verifies rejection by the real primary key; the original certificate still
   admits the deployment. No LIMIT or generation filter was added to conceal
   invalid cardinality.
2. **Compensation timezone comparison — scenario not reproduced; consistency
   improvement accepted.** PostgreSQL timestamptz is decoded as an aware datetime
   by the supported driver; aware UTC and local-zone datetimes compare by instant.
   Python equality does not raise the claimed naive/aware ordering TypeError.
   SQLite is not a supported adapter for this PostgreSQL journal. Nevertheless,
   compensation now uses the same explicit UTC/awareness decoder as fact state,
   and the actual correction→cap→compensation proof executes in Europe/Prague.
3. **Triggering assertion provenance — accepted and strengthened.** Relation
   seeds now require their triggering assertion ID; observation effects reject
   relation assertion metadata. The journal also checks the real assertion and
   normalization receipt: deployment, triggering claim, normalizer generation,
   predicate, and both canonical entity identities must match the seed. Tests
   use real normalization receipts and assertions rather than random IDs and
   reject a real assertion naming a different triple.
4. **Support fingerprint iteration order — accepted.** Evidence is sorted by
   claim UUID and role before support hashing. A PostgreSQL proof records the
   same support set in opposite iteration orders and checks identical digests.
5. **Duplicate claim load for candidate bounds — accepted.** Evidence validation
   now loads the claim once and uses that same mapping for its fingerprint and
   canonical endpoints.

## Supported-runtime CI and fixture correction

CI34066869642 completed on PostgreSQL19. Quality, unit, surfaces and adapters
passed. Workers reported 579 passed, 17 setup errors, and the known populated
conversion test failure. All 17 new journal cases stopped in their fixture:
its entity INSERT still included `type`, removed from the real schema by D96.
This demonstrates why the local PostgreSQL15 table-level probe was explicitly
not a full-schema proof. The fixture now uses the current untyped entity
identity schema; the local probe also applies the exact D96 entity-column
removal statements. No production schema or contract check was weakened.

The updated local table-level run passes 21 journal cases. The 16 new pure
legacy-conversion policy cases and 49 existing temporal cases pass together
(65 total). Updated full PostgreSQL19 CI and round-two review are still required.
The durable converter, complete runtime writer participation, cache routing,
forget/replay, T.2/T.3/T.5 and final release remain unfinished.
