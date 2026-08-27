# WP-I.3 + WP-I.4 resolution evaluation gate

**Status:** non-binding delivery evidence for WP-I.5
**Recorded:** 2026-08-27
**Binding decision:** D95 in `decisions.md` (entity identity is the real-world
referent)
**Binding design:** `plan/designs/entity_identity_and_retrieval_design.md`
§3.1–3.4 and §8
**Delivery gate:** `plan/plans/entity_identity_and_retrieval.md`, WP-I.5

## Why this record exists

WP-I.5 may change exact-lemma T0 from a verdict into candidate generation only
after the global resolver evaluation from WP-I.3 and the evidence-backed profile
behavior from WP-I.4 pass together. This note records that prerequisite. It does
not choose thresholds, amend D95, or claim production calibration.

## Evaluated cut

- `main` after WP-I.3: `81d5058ac6d35068f581fec5542ddc216c250485`
  (PR #312).
- WP-I.4 reviewed tip:
  `d983eeaa88bee4de7d1088aa0f021ec99726af5a` (PR #313).
- WP-I.4 squash on `main`:
  `f6e53d5845625592f72f39af013c6d7beb59a094`.
- GitHub Actions record:
  [CI run 33047974315](https://github.com/writeitai/remember-stack/actions/runs/33047974315),
  completed 2026-08-27.

The run exercised PostgreSQL 18 with the committed WP-I.3 evaluator and WP-I.4
profile implementation. The worker/spine lane passed the resolver suite and
profile integration proofs. The surface/eval lane passed on its unchanged
rerun with 677 tests passed and 2 skipped. Its first attempt had one unrelated,
documented transient LadybugDB citation-path overflow; the same exact Git tip
passed the complete rerun without an identity-code change.

## Resolver result

`src/tests/spine/test_resolver.py::test_resolution_suite_records_curves_and_blocks_on_regression`
ran `run_resolution_suite` over the committed eight-pair synthetic starter set
and asserted the persisted report:

| Measure | Result |
| --- | ---: |
| Global precision | 1.0 |
| Global recall | 1.0 |
| Evaluated pairs | 8 |
| False merges | 0 |
| False splits | 0 |
| Same-lemma/T0 negative canary | measured and false-merge-free |

The same test also proved that a same-lemma false merge blocks the suite even
when easy positives would dilute it above the global precision floor. The
deciding-tier diagnostics retain T3/T4 blame rather than treating T0
reachability as a match.

## Profile result

The same CI record passed the WP-I.4 acceptance behavior in
`src/tests/spine/test_profile_refresher.py`,
`src/tests/spine/test_resolver.py`, and the hard-forget suites:

- T3 compares mention plus claim context with an exactly attested
  `entity-profile-v2` vector, never a name-only vector.
- Empty or stale profile evidence cannot authorize T3 and escalates to T4.
- T4 receives the current profile summary and bounded salient facts.
- Same-name entities with different facts receive different profile inputs.
- Active survivors aggregate the redirect closure while merged members retain
  separately attested member-local profiles for neighborhood re-decision.
- Withdrawing or hard-forgetting evidence rebuilds or clears the profile, and
  forgotten text is absent from the remaining summary and embedding input.

## Gate conclusion and limit

The recorded I.3 + I.4 prerequisite **passes**, so WP-I.5 may implement the
binding D95 rule: exact T0 lists distinct active entity ids but never accepts
one as the referent. T3 may accept a sole current-profile candidate; empty,
conflicting, or multiple candidates reach T4; a T4 non-match may mint a second
entity with the same lemma and record a durable exclusion.

This starter result is a delivery canary, not a corpus-scale threshold claim.
The synthetic set is intentionally small, and the profile-space T3 thresholds
remain starting points to remeasure as adjudicated production examples grow.
It does not authorize automatic profile-space clustering, a production launch,
or any `t0_exact_accept` switch.
