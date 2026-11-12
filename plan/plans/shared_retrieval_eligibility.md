# D153 implementation and qualification

Architecture: [shared eligibility](../designs/shared_retrieval_eligibility_design.md).
Evidence: [SQL investigation](../analysis/retrieval_filtering_reliability_20261112.md).
Approval: [Opus round 3](../analysis/retrieval_eligibility_reviews_20261112/opus-r3-review.md)
on `431900b1d`, zero remaining blockers. Owner authorized implementation after
that approval. This file sequences work; accepted design does not establish
shipped behavior.

| Work | Status | Required completion |
| --- | --- | --- |
| Adopt/reconcile design | Accepted by the reviewed PR586 design | Binding D153, in-entry D149/D23 amendments, reconciled P1/entity/query-space/schema contracts; no runtime changes before approval |
| Independent references | Implemented; complete retained exports finishing | Dedicated old-head `p9_46_0067` database, identical deterministic/randomized fixtures and protected retained-store copies; no truncated oracle |
| Coordinate probes and index | Implementing approved ordered-bridge amendment | Constrained entity/bridge probes; online mention chunk index with session migration lock; any-ready-reading/latest-decision invariants; actual plans |
| Both testimony scope directions | Implemented; oracle and visibility checks pass | Candidate hydration and entity-driven nomination/budget share the same canonical mention relation; include explicit claim entity equality filters |
| Fact source/endpoint factoring | Implemented; complete row and D140 checks pass | One literal eighteen-column head-equivalent source helper; shared bounded endpoint rule; unchanged public rows/counts; all named consumers |
| Identity/catalog integration | Implemented; bridge AST/pins updating | Real D140 helper dependency hashes, new physical index/helper inventory, schema/capture/test head pins, manifest/deletion matrix and current benchmark pins |
| Qualification | In progress; native/8-reader prototypes pass, final index gates pending | Complete equality/visibility matrix, unrelated-row/120-partition work, eight-reader isolation/cancellation/accounting, full native fact+testimony completion, ACL/capture gates |
| Implementation review and merge | Pending | Required CI and Claude Opus implementation review, published checkpoints, precise remaining limitations |
| Retained-store operational validation | Pending | No ingestion; original runs unchanged, separate source/execution provenance, saved artifacts and verified GCS readback, current infra record |

Implement probes/coverage first and measure their plans, then fact factoring;
the runtime change is incomplete until the fact gates pass too. Keep original
reference SQL only in tests and historical migrations, never as a runtime
compatibility path. A failed probe hypothesis returns to design review rather
than changing source eligibility to improve timing.

The implementation uses current main and a separate draft PR after the approved
design merge. Local disposable PostgreSQL uses the existing RC1 image; every
test connection is explicitly bound to an owned test database. Retained original
benchmark stores are never reset or migrated for qualification. No model/provider
work is required for SQL tests or schema qualification.

Opus nonblocking implementation checks: register new helper names only in the
new migration/current registry, retain historical migration tuples unchanged;
emit the widened head fact-claim body literally and verify registered authored
definitions against migrated PostgreSQL; update current test head pins while
retaining intentional old-head oracles. Record index bytes, WAL, partition
planning, temp spill and eight-reader resource use without inventing throughput
claims. Autoboard checklist status is unverified because board credentials are
unavailable; no board approval is asserted.

The ordered document bridge amendment received Opus design approval at
`68e156154` ([review](https://github.com/writeitai/remember-stack/pull/587#issuecomment-6830637190)).
The accepted current contract is D153 §4.1. Negative ordinary UUID-order plans
and final expression-index prototypes remain in analysis. Finish distinct-key,
generic-plan, sole-index multi-deployment and native replacement-interruption
checks before the separate final implementation review.
