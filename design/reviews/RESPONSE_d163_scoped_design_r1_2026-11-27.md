# Response to the scoped D163 design review, round 1

Review of PR #682 at `1682f0598`. I accepted both P1 findings and both nits.

## P1

- **Manifest cutover.** Verified in v0.22.4 that stock setup publishes after
  the Alembic run and that an API bound to the old hash rejects the new one
  even after revalidation. Apply the online DDL with Alembic and run stock
  setup after draining old readers. Setup publishes and suspends active
  versions in its existing transaction. The migration gate runs an active
  customer saved query through the old API after every DDL commit and the
  same version through the new API after revalidation.
  [Setup](../../src/rememberstack/profiles/selfhost.py#L1169-L1175) ·
  [Publication](../../src/rememberstack/profiles/selfhost.py#L1248-L1268) ·
  [Registry admission](../../src/rememberstack/surfaces/query_sandbox/saved_queries.py#L1364-L1373) ·
  [Migration](../../plan/designs/shared_retrieval_eligibility_design.md#61-d163-migration)
- **Row multiplicity.** The deployed source view tests evidence with
  `EXISTS`, and final confirmation retains `WITH ORDINALITY`. The design
  requires at most one source row per fact and one confirmation row per
  eligible nomination. Compare actual gate rows with `EXCEPT ALL` both
  ways, including rank at final confirmation. Cover multiple qualifying
  links for both fact kinds and retain the repeated-nomination regression.
  [Source view](../../src/rememberstack/spine/migrations/versions/p9_48_0069_shared_eligibility.py#L315-L363) ·
  [Confirmation](../../src/rememberstack/surfaces/query_engine.py#L6231-L6249) ·
  [Regression](../../src/tests/spine/test_d153_eligibility_pg.py#L718-L743) ·
  [Qualification](../../plan/designs/shared_retrieval_eligibility_design.md#91-d163-fact-path-qualification)

## Nits

- Update the `v_memory_entity_survivor` comment with its direct walk and
  restore the deployed adapter comment on downgrade.
  [Deployed comment](../../src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py#L76-L77)
- Point the analysis to the redirect foreign key on line 34.
  [Foreign key](../../src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L34)

Full-schema equivalence, migration and latency remain implementation gates.
