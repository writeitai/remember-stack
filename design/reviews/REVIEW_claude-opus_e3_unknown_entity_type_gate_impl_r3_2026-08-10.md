# Implementation review (round 3): D86 E3 unknown entity type gate

**Verdict:** APPROVE_WITH_NITS
**Reviewer:** Claude Opus
**Date:** 2026-08-10

**Branch:** `fix/e3-unknown-entity-type-retry-drop` at `acf605a5`
**Fix commit under review:** `acf605a5` "fix(e3): D86 r2 — soft-success all-poison,
no double-bill, design align", on top of `e5d1d6d0`
**Prior reviews:**
[claude r1](REVIEW_claude-opus_e3_unknown_entity_type_gate_impl_2026-08-10.md) ·
[codex r1](REVIEW_codex-sol_e3_unknown_entity_type_gate_impl_2026-08-10.md) ·
[claude r2](REVIEW_claude-opus_e3_unknown_entity_type_gate_impl_r2_2026-08-10.md) (APPROVE_WITH_NITS) ·
[codex r2](REVIEW_codex-sol_e3_unknown_entity_type_gate_impl_r2_2026-08-10.md) (REQUEST_CHANGES)
**Binding design:** `plan/designs/e3_unknown_entity_type_gate_design.md` (D86),
`decisions.md:3316`

## Verification performed

Read the full `acf605a5` diff and the post-fix `workers/e3.py`. Traced the
billing question end to end through `workers/base.py:225-317` to establish which
exception classes can reach `_record_failed_provider_usage`. Checked the FK
classifier against the actual DDL (`p0_02_0003_entities_evaluation_e0_e1.py:34`,
`p0_02_0002_infrastructure_registries.py:219`) **and** against the installed
driver's error formatting (psycopg 3.2, `psycopg/pq/misc.py:135-139`,
`psycopg/errors.py:553-556`) to decide whether the substring match actually fires
in production. Compared the shipped soft-failure contract against the E1
precedent the design now cites (`workers/e1.py:344-376`, `:509-525`,
`:298-341`). Confirmed the replay marker is evidence-backed
(`spine/entity_registry.py:128-136`, `:199-205`). Verified the new `_mint` guard
cannot break the Postgres-backed resolver suite by checking that the test
bootstrap seeds `entity_types` from `CORE_MANIFEST`
(`spine/deployment_bootstrap.py:254`, `test_resolver.py:78-95`).

**Commands run.**

- `uv run pytest src/tests/workers/test_e3_unknown_entity_type_gate.py -q` →
  **11 passed** (1.41s)
- `uv run ruff check` on `workers/e3.py`, `spine/resolver.py`, the test file →
  **All checks passed**
- `uv run mypy workers/e3.py spine/resolver.py` → **both clean**; the test file
  still carries 5 `arg-type` errors (`:178`, `:211`, `:244`, `:279`, `:308`)

## Codex r2 blockers — both confirmed fixed

**1. Blocker: the no-progress breaker dead-lettered an all-soft version.**
**Fixed.** The `RuntimeError` is gone. The same four-term condition now logs
`e3.normalize_all_soft_failed` at ERROR and falls through to the terminal
follow-ups (`e3.py:217-229`, `:238-242`). The exact scenario codex named — one
claim, illegal `a1`, `ProviderInvalidResponseError` on `a2` — now returns a
`HandlerOutcome` with both branches enqueued, so `Worker.run_one` reaches
`self._ledger.complete(...)` (`base.py:283-286`) instead of `fail(retryable=True)`.
The design's §5/§6 now say this explicitly
(`e3_unknown_entity_type_gate_design.md:110-119`, `:140-150`), so the code and
the binding doc agree.

**2. Major (REQUEST_CHANGES driver): usage-bearing systemic failure billed twice.**
**Fixed.** The `:failure` meter is now gated on
`isinstance(exception, ProviderInvalidResponseError)` (`e3.py:457-465`). That
class is the only one `_is_claim_soft_failure` admits (`e3.py:505-513`), and
`_generate_normalize_response` is reachable only from `_normalize_claim`
(`e3.py:304-310`), which runs inside the handler's try (`e3.py:169-181`).
So a metered failure can never escape `handle`, and an escaping
`ProviderCallError` is never metered by E3 — the two accounting paths are now
disjoint by construction, not by convention. `base.py:299-307` remains the sole
authority for the escaping case. `test_systemic_provider_error_not_metered_in_generate`
(`test_e3_unknown_entity_type_gate.py:382-415`) pins the negative side and
`:345-379` the positive; the tier rename to `normalize_failed_response`
(`e3.py:463`) also closes r2 FINDING-4's `tier LIKE '%failed_response'` gap, with
no stale `normalize_failed` left in the tree.

## Disposition of the remaining r2 findings

| r2 finding | Status | Evidence |
| --- | --- | --- |
| **codex blocker 1** — all-soft dead-letter | **Fixed** | `e3.py:217-229`; design `:110-119` |
| **codex major 1** — double billing | **Fixed** | `e3.py:457-465`; test `:382-415` |
| codex major 2 — FK alarm over-classifies | **Fixed (with a caveat)** | `_is_entity_type_fk_violation` (`e3.py:516-526`), `type(exception).__name__` at `:196`. Real-constraint matching is thinner than it looks — **FINDING-3** |
| codex major 2 — bounded log labels | **Fixed for per-label width; count still unbounded** | `_bounded_type_label` now routed through all three log sites (`e3.py:320`, `:403-406`, `:485`); count uncapped (`e3.py:495-497`) — **NIT-1** |
| codex major 3 — mint guard untested | **Fixed** | `test_mint_refuses_unregistered_type_before_insert` (`:418-444`) calls the real `CascadeResolver._mint`; deleting the guard makes it fail (the fake connection cannot serve `_INSERT_ENTITY`'s downstream path) |
| codex major 3 — no `handle`-level test | **Open** | Still no test constructs `handle`; now covers a branch that this commit changed — see **Test gaps** |
| claude r2 FINDING-1 — design contradicts code | **Fixed** | §5 `:110-119`, §6 `:140-150`, cost-key table `:129`, `:132-134` |
| claude r2 FINDING-2 — FK alarm imprecise, wrong `error_class` | **Fixed** | `e3.py:191-198`. The `UnregisteredEntityTypeError` branch still logs the class constant (`e3.py:188`), which is harmless there — the class is exact |
| claude r2 FINDING-3 — breaker asymmetry | **Moot** | The breaker is gone. The partial-outage case is now readable from `e3.claims_processed count=N soft_claim_errors=M` (`e3.py:212-216`) |
| claude r2 FINDING-4 — tier naming | **Fixed** | `normalize_failed_response` (`e3.py:463`) |
| claude r2 FINDING-5 — unbounded labels | **Partial** | See NIT-1 |
| claude r2 MINOR-1 — retry crash discards attempt-1 facts | **Open, sharper** | **FINDING-2** |
| claude r2 MINOR-4 — troubleshooting page | **Open** | `website/src/app/docs/troubleshooting/page.mdx` still has no unknown-type / zero-observations entry |
| carried nits — design cross-link, Rule 2 §12, test mypy, `assert` | **Open** | **NIT-2..NIT-5** |

I re-derived the two fixed blockers rather than taking the commit message for
them, and found no correctness defect in round 3.

## Findings

### FINDING-1 (major, design completeness) — the soft-success path adopts half of the E1 precedent it cites

The design now justifies the narrow soft class as "same pattern as E1 chunk
poison" (`design:118-119`), and the predicate is indeed a faithful mirror of
`_is_provider_outage` (`e1.py:509-525` vs `e3.py:505-513`). But E1 does not stop
at classifying. Every chunk it soft-skips gets a **durable typed skip row** —
`_stamp_skips(..., skip_code="poison_chunk")` (`e1.py:317-325`) — and readiness
is then proved against `vectors ∪ closed_skips`, raising if anything is neither
embedded nor explicitly skipped (`e1.py:327-341`). The skip is a database fact an
operator can query.

E3's soft drop writes nothing. After this commit, a version whose every claim hit
content poison is `succeeded` in the ledger, has zero relations and zero
observations, and its only trace is one ERROR log line (`e3.py:226-229`). That is
the outcome codex asked for and I agree it beats dead-lettering the document —
but the reason E1 can afford the same choice is the skip row, and E3 has no
equivalent.

Two things make this a documentation-and-completeness gap rather than a
correctness defect, and both deserve to be written down because a stranger
reading the design cannot currently derive either:

- **Recovery works, for a non-obvious reason.** `normalized_claim_ids` is
  evidence-backed — it reads `relation_evidence ∪ observation_evidence`
  (`entity_registry.py:199-205`), not a "claim was attempted" marker. A
  soft-failed claim therefore never counts as normalized, so a replay (design §7,
  `:152-161`) re-processes exactly the lost claims. The recovery contract is
  real; it is nowhere stated.
- **The alarm is not registered as one.** `e3.normalize_all_soft_failed` appears
  only in §5 prose (`design:113-114`). §8's event table (`design:170-176`) and its
  FK-alarm paragraph (`:181-182`) — the section an operator or a metrics
  implementer reads — do not list it. The one signal that distinguishes "clean
  empty extract" from "the normalize model is returning garbage corpus-wide" is
  absent from the observability contract.

**Ask:** add `e3.normalize_all_soft_failed` to the §8 table as an alarm-grade
event next to the FK alarm, and state the replay contract in §7 ("soft-failed
claims are not marked normalized because the marker is evidence-backed;
re-enqueueing the version re-derives them"). Consider — as design content, not a
phase — whether E3 should stamp a durable per-claim soft-drop record the way E1
stamps `poison_chunk`; if the answer is no, §6 should say why the log-plus-replay
pair is sufficient here.

### FINDING-2 (minor) — a crash on the retry discards attempt-1's legal facts; a *bad* retry response does not

`_generate_normalize_response` keeps the last successful response in a local
(`e3.py:440`, assigned `:468`). When the budget is exhausted normally, it breaks
and returns that response (`e3.py:487-488`, `:501-502`), and the deterministic
gates then drop only the illegal-bearing assertions and keep the legal siblings —
which is exactly what §5's full-replacement rule intends and what
`test_normalize_claim_keeps_legal_sibling_observation` (`:300-327`) pins.

When attempt 2 *raises* `ProviderInvalidResponseError` instead, `e3.py:466`
re-raises and the whole claim is skipped — attempt 1's legal relations and
observations are discarded along with its illegal ones, even though they are
sitting in `response`.

So the claim's blast radius depends on *how* the retry failed rather than on the
content: a garbage-but-parseable retry keeps the legal half, an unparseable retry
loses everything. Before this commit that asymmetry ended in a loud dead-letter;
now it ends in a silent success, which is why it is worth closing. The fix is
local — on `ProviderInvalidResponseError` with `attempt > 1 and response is not
None`, break instead of raising — and it makes the two exhaustion paths agree.
Whichever way it is decided, §5 should state which response is used when the
retry itself fails.

### FINDING-3 (minor) — the FK classifier's constraint-name branch cannot match the real constraint

`_is_entity_type_fk_violation` (`e3.py:516-526`) tries two routes. I checked both
against the real system:

- **Message route (works).** The `entities` FK is declared unnamed in raw DDL
  (`p0_02_0003_entities_evaluation_e0_e1.py:34`), so Postgres emits the incident's
  violation with `DETAIL: Key (deployment_id, type)=(…, Process) is not present
  in table "entity_types".` psycopg 3 keeps that DETAIL in the exception string —
  `get_error_message` only strips the severity prefix
  (`psycopg/pq/misc.py:135-139`, used by `errors.py:553-556`) — so `"entity_types"
  in str(error).lower()` does fire for the D86 incident class. Good.
- **Constraint route (dead for this constraint).** Because the constraint is
  unnamed, Postgres generates `entities_deployment_id_type_fkey`, which does not
  contain the substring `"entity_type"` the branch looks for (`e3.py:524`). The
  fallback exists but cannot fire for the very violation it was written for.

The whole alarm therefore rests on a driver's message formatting. The unit test
does not protect it: `test_entity_type_fk_violation_classifier` (`:447-460`)
feeds a hand-written string (`'constraint "entities_deployment_id_type_fkey" on
entity_types'`) that is not Postgres's wording, so it would still pass if real
DETAIL text stopped matching.

Cheap and durable alternative: classify on `sqlstate == "23503"` plus
`diag.table_name == "entities"` and `diag.column_name`/`constraint_name`, or name
the constraint in a migration so the name route is meaningful. Behaviour is
unaffected either way — the `raise` at `e3.py:198` is unconditional, so
misclassification only silences the alarm.

Related, unchanged from r2 and still worth one line: when the classifier returns
`False`, E3 logs nothing at all for that `IntegrityError`. The worker still logs
it (`base.py:235-240`), so nothing is lost, but a distinct
`e3.claim_integrity_error` would keep the E3 event stream self-describing.

### FINDING-4 (minor, D66) — the pipeline page's soft-drop list is now incomplete

`website/src/app/docs/ingestion/pipeline/page.mdx:27` enumerates the re-derivable
soft drops as "unknown predicates, signature rejects, and unknown entity types
after the E3 type-gate retry budget (D86)". This commit added a fourth member of
that set with a stronger user-visible consequence: a claim whose normalize call
returns unparseable structured output is dropped inside a *successful* stage, and
if every claim does so the version completes with zero facts. An operator reading
that page to explain "normalize succeeded, observations are 0" will not find the
case that produces it. One clause, same PR.

r2's MINOR-4 also remains: `website/src/app/docs/troubleshooting/page.mdx` has an
"Ingestion stuck or failed" section but no entry for the symptom D86's own soft
path produces, and no pointer to the `e3.unknown_entity_type*` /
`e3.normalize_all_soft_failed` greps that resolve it.

### NITs

- **NIT-1 — illegal-label *count* is still unbounded.** Per-label width is capped
  at 48 chars and now applied to every log site, but `e3.py:495-497` joins every
  distinct illegal token into the retry prompt and the log sites emit a list of
  all of them. `NormalizationResponse.relations`/`.observations` are plain
  unbounded tuples (`model/relations.py:56-57`), so the count is limited only by
  the model's output budget. r2's suggestion still applies: cap at ~10 plus an
  `+N more` bucket. Also only `\n` is normalized (`e3.py:531`); `\r`/`\t` pass
  through.
- **NIT-2 — five mypy `arg-type` errors in the test file** (`:178`, `:211`,
  `:244`, `:279`, `:308`), all `dict` invariance on the canned payloads;
  annotating them `dict[str, object]` clears it. `e3.py` and `resolver.py` are
  mypy-clean.
- **NIT-3 — `assert response is not None` as control flow** (`e3.py:501`). Under
  `python -O` the assert vanishes and `None` is returned into
  `_normalize_claim`'s attribute access.
- **NIT-4 — one-element list comprehension** at `e3.py:403-406`;
  `[_bounded_type_label(value=observation.subject.type)]` reads the same and is
  one line.
- **NIT-5 — carried doc housekeeping.** No D86 cross-link in
  `e2_e3_claims_relations_design.md` even though the design's header claims to
  amend it (`design:10`); the §12 implementation checklist (`design:212-219`) and
  the "defer" row (`design:210`) are build-sequencing content that CLAUDE.md
  Rule 2 puts in `plan/plans/`, which still has no D86 entry.

## Test gaps residual

11 tests pass. The two added this round are the right ones — the mint guard is
now genuinely exercised (`:418-444` calls the real `_mint`; the guard is the
first statement at `resolver.py:436-446`, so deleting it fails the test), and the
no-double-bill invariant is pinned from both sides (`:345-379`, `:382-415`).

I also checked the risk r2 flagged as "CI is the first real execution" of the
mint guard's SQL: `test_resolver.py:132` mints `Person` through the real
`CascadeResolver` against Postgres, and its autouse fixture bootstraps the
deployment (`test_resolver.py:78-95`), which seeds `entity_types` from
`CORE_MANIFEST` (`deployment_bootstrap.py:254`). So `_SELECT_ENTITY_TYPE_EXISTS`
will execute in CI and the guard will not break the existing suite.

What is still uncovered, against design §9 (`:186-194`):

| Case | Covered? | Gap |
| --- | --- | --- |
| N claims, one always-illegal → other N−1 process, terminal branches enqueued, job succeeds | ❌ | **Still the D86 invariant and the BEAM regression test.** No test constructs `handle` |
| All claims soft-fail → job succeeds, `e3.normalize_all_soft_failed` logged | ❌ | The branch this commit *added* (`e3.py:217-229`) has no test in either polarity. r2 criticised the breaker for being untested; the replacement inherited the gap |
| Systemic error mid-loop escapes `handle` | ❌ | `_is_claim_soft_failure` is unit-tested (`:331-342`), but nothing proves the loop actually re-raises |
| All-legal → single generate, no retry | ❌ | No test asserts exactly one `generate` on the clean path |

One `handle` test with a fake claim catalog and a scripted provider would close
the first three at once, and it is the single highest-value addition left: the
all-soft branch is now the one place where E3 converts a total-poison run into a
green ledger row, and nothing regression-protects that decision in either
direction.

## Recommendation

**APPROVE_WITH_NITS.** Both round-2 blockers are fixed in the shape codex asked
for, and I verified each by re-deriving the behaviour rather than reading the
commit message: the all-soft version now completes through
`ledger.complete(...)`, and the metered-vs-escaping billing paths are disjoint by
construction. The design was amended to match the code, so §5/§6 no longer teach
a stranger the wrong isolation contract. No correctness defect found this round.

Before merge (both are doc edits):

1. **FINDING-1** — register `e3.normalize_all_soft_failed` in §8 as an
   alarm-grade event and state the replay contract in §7. The system now leans on
   a single log line to distinguish a corpus-wide model outage from an empty
   extract; that has to be in the observability section, not only in §5 prose.
2. **FINDING-4** — one clause on `pipeline/page.mdx:27` adding claim-level
   structured-output poison to the soft-drop set (D66 same-PR docs).

Strongly recommended, same PR or immediate follow-up:

3. The `handle`-level test covering N-claims continuation and the all-soft branch
   — the only untested path that decides whether a version job succeeds.
4. **FINDING-2** (align the two retry-exhaustion outcomes) and **FINDING-3**
   (classify the FK alarm on `sqlstate`/`diag` rather than message text).

NIT-1..NIT-5 are housekeeping and need not gate the merge.
