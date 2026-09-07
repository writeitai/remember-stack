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

## Round two and durable-converter review boundary

Antigravity reviewed `26461a9a0039043160bd93963ce6fc377b62c032` and
**approved the scoped journal and pure conversion policy**, explicitly without
approving full PR #384. Output: `/tmp/rs-t1-journal-antigravity-r2.log`.
It withdrew the generation primary-key finding and accepted the provenance,
support-order and timezone fixes. It independently ran the 65 then-current
pure tests. Its remaining clarification is now explicit: the cause of a legacy
world-time cap is separate from a later belief withdrawal. Four additional
policy cases cover that distinction and uncertain/undated boundaries (69 pure
tests total).

[CI34067544140](https://github.com/writeitai/remember-stack/actions/runs/34067544140)
ran the corrected fixtures on PostgreSQL19: workers reported 600 passed and
one failure, the known populated-upgrade path that still bypasses conversion.
All 21 committed journal cases passed. Quality, unit, surfaces and adapters
passed; contract smoke, workers and Compose remained failed. This is scoped
evidence, not a passing PR.

The subsequent durable catalog and streaming journal changes require a new
review. Local PostgreSQL15 now passes 38 database cases, including 15 converter
cases, all original journal cases, a 1025-witness conversion and rollback after
a cursor fails beyond the first inserted evidence batch. The probe uses actual
legacy constraints, the D96 entity column removal, and temporal expansion DDL;
it does not exercise the full supported PostgreSQL19 Alembic graph. The
converter's predecessor-cap test also verifies that the consumed successor seed
claim is in the operation's deletion/support inventory.

## Round three and supported converter evidence

Antigravity reviewed `5dfe139a` and **approved the concrete converter and
streaming journal scope**, without approving full PR #384. The completed output
is `/tmp/rs-t1-converter-antigravity-r3.log`. It independently ran Ruff,
targeted Pyright and the 69 pure temporal cases. Its downstream guidance is
retained: startup must drive every bounded phase until the recorded phase
changes, and full supported-runtime CI remains mandatory before merge.

[CI34069535978](https://github.com/writeitai/remember-stack/actions/runs/34069535978)
completed on PostgreSQL19 with **617 worker/spine cases passing and one failure**.
All 38 journal/converter cases passed on the complete schema. The sole worker
failure was the D79 fixture's direct upgrade to head without conversion. Quality,
unit, surfaces and adapters passed; contract smoke and Compose retained the
same startup gap. The following startup change replaces that direct path with
the actual converter rather than bypassing its guard. That new change requires
its own CI and Antigravity review.

## Round four and startup CI findings

Antigravity completed its review of `ec21eb23` with **scoped approval** of
startup, empty bootstrap, exact constraint checks and finalization. Output:
`/tmp/rs-t1-startup-antigravity-r4.log`. It ran Ruff, targeted Pyright, the 90
temporal/profile cases, and documentation typechecking. It explicitly retained
the operator obligation to stop old binaries and the unfinished runtime scope.

[CI34070676550](https://github.com/writeitai/remember-stack/actions/runs/34070676550)
passed contract smoke, quality, unit, surfaces, adapters and the docs build.
Workers reported 629 passes and one failure: a repeated full-schema teardown
reused a pooled statement with a dropped enum OID. The isolated upgrade fixture
now disposes its pool before rebuilding the schema. Production upgrade never
performs that full teardown. All other startup tests, including the pre-C drain
proof omitted locally, passed on PostgreSQL19.

Compose completed fresh startup, the zero-cost pipeline and gated restart. Its
final assertion still expected the old `p9_27_0048` head instead of `p9_30_0051`.
The assertion now names the actual head and additionally checks the explicit
completed zero-row conversion and fact-generation certificate. No runtime
guard or lifecycle assertion was removed. Updated CI remains required.

The follow-up `7abb2714` completed
[CI34071365059](https://github.com/writeitai/remember-stack/actions/runs/34071365059)
successfully. All **630 worker/spine tests** passed on PostgreSQL19. Compose
fresh startup, the zero-cost pipeline, gated restart and explicit conversion
certificate checks passed, as did contract smoke, unit, quality, surfaces,
adapters and the documentation build. This verifies the implemented startup
increment; it does not complete the remaining T.1 runtime program.


## Normalization publication review and integration follow-up

Antigravity round five reviewed committed publication scope `519c3453` and
returned **SCOPED APPROVED**, while explicitly withholding PR merge approval.
The completed output is `/tmp/rs-t1-normalization-antigravity-r5.log`; the
original process handle completed normally. Its pending PostgreSQL19 prerequisite
subsequently passed in
[CI34072158644](https://github.com/writeitai/remember-stack/actions/runs/34072158644).

The review's defensive predicate-row locking and explicit active-forget and
observation-only proof requests are applied in the next working increment.
Independent comparison with binding D110 §3.1 also found that publication must
retain the normalizer's shape judgment, not overwrite it with D41 precedence.
The model/catalog/test now retain that judgment; D41 precedence remains an
application responsibility. Round five's praise of publication-time precedence
is therefore not adopted as authority over the binding contract.

The next increment wires the worker and closed-version handoffs, with real
worker/catalog and database rollback evidence recorded in the progress report.
Its ordered relation application is still unfinished and explicitly fenced;
this increment must not be represented as a passing complete pipeline or a
merge-ready PR. The local integration requires its own scoped review and later
supported full-pipeline validation after the remaining writers are connected.

## Round six: closed-version handoff

Antigravity reviewed `267a9d8e` and returned conditional scoped approval with
required corrections. The original process completed normally; output is
`/tmp/rs-t1-handoff-antigravity-r6.log`. Some review prose named nonexistent
helpers, tests and an `apply_relations` stage. Those names are not implementation
evidence. Independently inspecting the actual code confirmed three findings:

- D56 sibling versions inherited the primary version's hash/lane. Completion
  now selects and preserves each sibling's own source-version hash and exact
  extraction lane. The new real-ledger regression passes.
- Claim completion checked work state after expensive materialization locks.
  It now validates exact running work coordinates after the shared admission
  prefix and before those locks, retaining the required lock order.
- Missing representation lookup leaked a bare row-count error. It now reports
  a typed temporal conflict.

The working ordered-applier increment has 18 scoped PostgreSQL15 proofs; the
normalization suite now has 22. It requires its own review and supported CI.
The D111 unknown-start constraint conflict and multiple dated identity ambiguity
are separately recorded in the progress report; scoped handoff approval does
not settle those design decisions or certify the full pipeline.

## D111 design review

Antigravity completed read-only review of design commit `40609949` with
**design-level approval and zero blockers**, output
`/tmp/rs-t1-d111-antigravity-r1.log`. It required implementation evidence for
recorded start-acquisition refusal and populated final-constraint installation.
PR #385 passed [CI34076169182](https://github.com/writeitai/remember-stack/actions/runs/34076169182)
and CLA, and merged as `7b927293`. Code-path integration jobs were skipped by
the design-only path filter; the design approval is not runtime acceptance.

The subsequent D111 implementation applies its predicate to the actual migration,
schema verifier and pure neighbor guard, with the scoped evidence recorded in
the progress report. Antigravity round seven separately reviews ordered writer
commit `ada8d28b` (rebased equivalent `77a248c1`); its review was requested before
the D111 implementation and must not be cited as approval of those later changes.

## Round seven and current-head CI

Antigravity completed its `ada8d28b` review with changes requested for a claimed
41-error fixture-import lint failure. Its output is
`/tmp/rs-t1-application-antigravity-r7.log`. The machine's unpinned system Ruff
is 0.11.2; this repository and CI use locked Ruff 0.15.20. The actual
`uv run ruff check src/ benchmarks/` passes, including the explicit fixture
re-exports. Removing those imports as suggested would remove pytest fixture
registration. The review's nonexistent helper names and inconsistent test
counts are not adopted as implementation evidence or full acceptance.

Current-head [CI34076741576](https://github.com/writeitai/remember-stack/actions/runs/34076741576)
instead reports a formatting error in `spine/supersession.py`'s long generation
declaration. The checked job log confirms that exact failure. The declaration
is now formatted using locked Ruff; repository-wide lint and format checks pass
(486 files). The remaining supported PostgreSQL jobs must still be assessed.
Round eight must verify the review disposition and D111 implementation; round
seven's conditional verdict is not represented as approval.

## Round eight

Antigravity completed review of `0a9fcec7` and granted **scoped approval** of
the round-seven tooling disposition, locked formatting fix and D111 implementation
`8c4b8a24`. Output: `/tmp/rs-t1-d111-implementation-antigravity-r8.log`. It verified
the known-start predicate across final migration, exact schema check and pure
guards, fact generation, and 23 scoped PostgreSQL15 application cases. It
explicitly retained incomplete full-pipeline and program gates.

The subsequent readiness/protocol/provenance-fixture corrections address actual
CI34076741576 failures and need their own supported CI and review. They are not
covered by round eight. The separate D112 design review remains pending.

## Round nine and D112 design disposition

Antigravity completed review of `978c9885` with **scoped approval** for readiness,
fixture provenance and Full-v25 generation changes. Output:
`/tmp/rs-t1-readiness-antigravity-r9.log`. It independently ran locked lint/format,
full-library Pyright, 195 benchmark tests, 72 temporal tests, the 24-application
and 22-normalization PG15 probes, and the docs build. Its cosmetic v24 test-name
and docstring observations are corrected in the following D112 increment.
Supported [CI34077867523](https://github.com/writeitai/remember-stack/actions/runs/34077867523)
subsequently passed every job; the review's then-pending CI statement is superseded
by that observed result.

Antigravity separately approved D112 design commit `2f64a784` with zero blockers;
output `/tmp/rs-t1-d112-antigravity-r1.log`. PR #386 passed its design checks and
merged as `a7d304be`. Implementation is rebased onto that main. Neither approval
certifies the subsequent complete-target implementation or full T.1 program;
that increment needs its own review and supported CI.

## Round eleven: ordinary correction foundation

Antigravity reviewed `e40f838a` after its rebase onto main `b776b3e5` and granted
scoped approval, with no blocking findings. It checked canonical endpoint
authority, current linked support, distinct source lineages, candidate-only
output, context completeness, confidence thresholds and the existing monotonic
window/neighbour guards. Its focused tests, locked lint/Pyright and inventory
checks passed. One host MIME mapping failure in its extra full unit run was
outside the correction scope; all jobs of supported CI34081321723 passed.
The correction worker, atomic preparation/application, compensation and
read/explain integration remain unimplemented; this is not full-program approval.

## D113 observation preparation and inference — R12 through R15

R12 approved the earlier source-membership/admission scope, independently
rerunning 22 PostgreSQL 15 membership/admission cases, 38 journal/conversion
cases, the actual expansion probe and static checks.

R13 found that retaining old completed plans inside the next prepared input
snapshot did not meet the ordinary operation-log disposition contract. Its
first suggested resolution did not explain full-payload storage. R14 clarified
that the existing operation table has no rejected-model JSON column; its
suggestion to skip first-mention audit was rejected because D110 still
requires a stale disposition and the schema permits historical logical targets.

The revised implementation uses the exact retired preparation UUID as operation
UUID and retains its input digest, stale result, unchanged historical state,
source witnesses and non-mutating block footprint. It creates neither a fake
fact nor a fact-plane adjudication referencing a missing row. Rejected model
JSON is not needed for replay of an unapplied answer and is not retained in
the next input snapshot. Ordinary seed application rejects stale seeds; the
separate diagnostic entry point rejects state changes and applied results.

R15 independently checked that implementation against D110 §2 and D113 §3,
confirmed that no stronger rejected-payload retention obligation appears in
the accepted contract, and reported no blockers in the revised increment
(code committed as `575f04cb`). Its validation: 20 spine identity/re-split
unit tests, three core identity tests, eight real-receipt PostgreSQL 15
preparation/CAS/diagnostic proofs, 38 journal/conversion proofs, locked Ruff
and full Pyright. Local combined fact/correction/observation unit validation
also passed all 79 cases.

The full dependent effect planner, atomic observation applier/support moves,
worker handoffs, source/cache/forget lifecycle and consumer packages remain
unfinished. These scoped review results do not approve #384 for merge or release.

## D113 planner and assignment verification — R16 through R19

R16's type narrowing and D54/D55 conflation findings were applied in `13797a8f`.
R17 approved that revised planner/preparation scope, independently rerunning
41 observation unit cases, ten private PostgreSQL preparation proofs and static
checks. That approval did not cover the subsequent executor.

R18 reviewed the current-assignment reader. Its JSON-null features finding was
fixed with an explicit object check and corruption test. Move payload parsing
now uses direct typed validation; no datetime fields require a JSON round trip.
Actual cross-generation A→B→A execution, rollback, moved-receipt reuse and altered
move authority tests replace the earlier missing execution coverage. A cycle
test deliberately recomputes a matching corrupted inventory digest, ensuring
the semantic cycle itself is refused.

R18 also recommended rejecting every invalidated assignment target. That
interpretation was declined: current evidence assignment describes where a
retained assertion supports a historical identity; it does not assert that the
identity remains a current belief. Live candidate nomination excludes invalidated
facts. Reusing a historical D55 receipt verifies its retained evidence and owner
without resurrecting it. The real source-withdrawal execution test checks both
behaviors, including refusal of corrupted historical ownership.

Connecting that test to PostgreSQL exposed an existing design ambiguity: a new
identity cannot be invalidated before it was learned. Independent analyses
converged on an empty belief interval at recorded creation for already-withdrawn
historical support. R19 approved the narrow clarification in
`temporal_clocks_design.md` §4.4, with no new schema, clocks, queues or settings.
Its wording correction was applied. The planner preserves the actual prior
withdrawal cause/time in existing operation-linked adjudication features;
ordinary existing-fact closure is not universally clamped. Pure tests cover
withdrawal on either side of creation; the real D55 test covers atomic creation,
world-date preservation, retained provenance, live nomination and exact retry.

R20 approved the combined executor, assignment reader, shared journal
conservation guards and these dispositions, with two nonblocking suggestions:

- Direct Python dictionary validation was declined for preparation/plan models:
  their strict UTC datetime fields accept ISO strings in JSON mode but reject
  those strings in Python mode. A direct reproduction confirms `datetime_type`
  errors with `model_validate` and exact equality with `model_validate_json`.
  Unlike these timestamp-bearing models, support-move payloads do use direct
  dictionary validation.
- The outer canonical block lock was retained and documented: it serializes
  candidate discovery and completed receipt reads before the application row
  lock. The inner session adds discovered fact locks under that prefix.

R20 reviewed the 20-case execution snapshot. A subsequent local cap check fix
retains D107's ending-occurrence authority and distinguishes source-supplied
incoming dates from existing-successor starts; the added real resignation test
brings local execution proofs to 21. This follow-up does not alter the accepted
design. Full worker/lifecycle/cache/forget integration and supported acceptance
remain required. The user's explicit merge/release approval remains a separate
final gate.

## R21: ending-occurrence cap and rebase follow-up

Antigravity approved the narrow follow-up at `e61eb0fe`, with no blocking
findings. It checked cap execution against the planner and identity contract:
incoming testimony supplies its world-time instant (including a dated ending
occurrence), while a historical incoming state uses its existing successor's
locked start. The source's current identity must participate in the cap.

The reviewer also checked that the rebase onto main `c0f5c010` preserved #382's
Codex subscription adapter and provider-specific model/effort/temperature pins
under the Full-v25 temporal roll. It independently passed 63 adapter/protocol
tests plus static/import/inventory checks, and inspected the 21 execution and
152 benchmark proof results. R20's strict-UTC JSON parsing and canonical-lock
dispositions were confirmed. This review does not approve the still-unfinished
worker/lifecycle/cache/forget program or authorize merge/release.
