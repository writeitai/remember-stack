# Implementation review (round 2): D86 E3 unknown entity type gate

**Verdict:** APPROVE_WITH_NITS
**Reviewer:** Claude Opus
**Date:** 2026-08-10

**Branch:** `fix/e3-unknown-entity-type-retry-drop` (`e5d1d6d0`) vs `origin/main`
**Fix commit under review:** `e5d1d6d0` "fix(e3): address D86 impl dual review
(narrow isolation + mint guard)", on top of `993f989e`
**Round-1 review:**
[REVIEW_claude-opus_e3_unknown_entity_type_gate_impl_2026-08-10.md](REVIEW_claude-opus_e3_unknown_entity_type_gate_impl_2026-08-10.md)
**Binding design:** `plan/designs/e3_unknown_entity_type_gate_design.md` (D86),
`decisions.md` D86

**Verification performed.** Read the post-fix `src/rememberstack/workers/e3.py`
in full and the `spine/resolver.py` / `spine/__init__.py` / `website/` diffs
(`git diff origin/main...HEAD`). Checked the new mint guard's SQL against the
`entity_types` DDL (`spine/migrations/versions/p0_02_0002_infrastructure_registries.py:206-218`).
Traced the re-raise path through `workers/base.py:233-317` to confirm what the
outer ledger now does with each escaping class. Checked which provider errors
actually carry `usage` (`adapters/openrouter.py:184-250`, `:286-289`) to test
the double-metering question. Compared the narrowed soft-failure predicate
against the existing repo precedent (`workers/e1.py:509-524`,
`workers/e0.py:866-880`). Grepped `entities` insert paths, `IntegrityError`
handlers, `resolve_t0` callers, `plan/plans/` for D86, and the website docs tree.

**Commands run.**

- `uv run pytest src/tests/workers/test_e3_unknown_entity_type_gate.py -q` → **9 passed** (1.48s)
- `uv run pytest src/tests -q -k "resolver or e3 or entity"` → 25 passed, 36 skipped
  (Postgres-backed tests skip locally; they run in CI)
- `uv run ruff check` / `ruff format --check` on the three changed source files → clean
- `uv run mypy` on the three changed source files → `e3.py` and `resolver.py`
  clean; **5 errors remain in the test file** (round-1 NIT-2, unresolved and now
  larger)

## Summary

Both round-1 blockers are genuinely fixed, not papered over.

The claim catch is now narrow and typed: only `ProviderInvalidResponseError` is
soft-isolated (`e3.py:198-209`, predicate at `e3.py:494-502`), and everything
else — provider outages, `TimeoutError`/`OSError`, database errors, plain bugs —
re-raises to the work ledger. That predicate matches the convention the repo
already uses for chunk-attributable poison in E1 (`workers/e1.py:509-524`), so
the two soft-isolation sites now read the same way. Usage-bearing failures are
metered before the raise (`e3.py:446-455`), closing the round-1 billing gap. And
the whole-handle no-progress breaker (`e3.py:215-226`) means the specific
scenario round-1 called worse-than-the-incident — every claim fails, job marked
`succeeded` empty, nothing ever retries — now raises and goes through the outer
ledger's retry/dead-letter path (`workers/base.py:233-270`).

The `CascadeResolver` mint refusal is real: `_mint` selects the type from
`entity_types` before `_INSERT_ENTITY` and raises a typed
`UnregisteredEntityTypeError` if absent (`resolver.py:436-446`, SQL at
`resolver.py:714-722`). I checked the predicate against the DDL —
`entity_types` is keyed `PRIMARY KEY (deployment_id, type)`
(`p0_02_0002_infrastructure_registries.py:206-218`), so the column names bind and
`one_or_none()` cannot raise `MultipleResultsFound`. The guard deliberately
ignores `status`, which is consistent with `entity_type_parents` being the
allow-list source (design §4) — the gate and the guard admit exactly the same
set, which is what makes them provably FK-safe together. E3 turns that error
into the design's §8 alarm and re-raises (`e3.py:182-190`), so "the gate broke"
is now loud and detectable, which was the whole point.

The metrics gaps closed: `claims_processed` with a soft-error count
(`e3.py:210-214`), `site=` on the unknown-type and drop events
(`e3.py:313-318`, `:396-401`, `:469-475`), `error_class` on the error event, and
the `e3.entity_type_fk_violation` alarm. The false docs sentence is fixed
(`website/src/app/docs/ingestion/pipeline/page.mdx:27`) and now draws the right
line — systemic → dead-letter, re-derivable assertion drops → logged inside a
successful stage.

Tests went 3 → 9 and now actually exercise the incident path: illegal
observation dropped with zero `resolve` calls, illegal relation dropped *before*
`ensure_other_predicate` (this one pins the ordering round-1 flagged as
correct-but-unpinned), legal sibling kept, and the `:a1`/`:a2` cost keys
asserted against a recording meter.

What is left is smaller than round 1 and none of it is a correctness blocker.
The binding design still says the opposite of the code in two places (§5, §6)
and does not record the two mechanisms this commit invented (the no-progress
breaker, the `:failure` billing key) — a doc edit, but a binding one in this
repo. The FK alarm fires on *any* `IntegrityError`, not just an entity-type one.
The new breaker — a path that converts success into failure — has no test. And
the round-1 minors and nits are almost all still open.

## Round-1 disposition

| Round-1 item | Status | Evidence |
| --- | --- | --- |
| **BLOCKER-1** — blanket `except Exception` swallows systemic failure | **Fixed** | `e3.py:198-201` re-raises unless `_is_claim_soft_failure`; predicate is `ProviderInvalidResponseError`-only (`e3.py:494-502`); matches the E1 precedent (`e1.py:509-524`) |
| BLOCKER-1a — usage-bearing provider errors unmetered | **Fixed** | `e3.py:446-455` meters `normalize:{id}:aN:failure` before re-raise; test `test_e3_unknown_entity_type_gate.py:344-378` |
| BLOCKER-1b — fully-failed job indistinguishable from clean job | **Fixed** | `e3.py:215-226` raises `e3.normalize_no_progress`; per-job counts logged at `e3.py:210-214` |
| **BLOCKER-2** — `CascadeResolver` mint refusal missing | **Fixed** | `resolver.py:41-42` typed error; guard `resolver.py:436-446`; SQL `resolver.py:714-722` verified against the `entity_types` PK; exported at `spine/__init__.py:41`, `:62` |
| BLOCKER-2a — `e3.entity_type_fk_violation` alarm missing | **Fixed (imprecise)** | `e3.py:182-197` — fires, but on any `IntegrityError`; see **MINOR-2** |
| **MAJOR-1** — D66 docs; "never silent skip" false | **Mostly fixed** | `page.mdx:27` rewritten correctly. Troubleshooting playbook entry still absent (`docs/troubleshooting/page.mdx`); `/docs/project-status` contains no E3 claim, so nothing there is untrue. See **MINOR-4** |
| **MAJOR-2** — no denominator, no `site`, no FK alarm | **Fixed (partial value)** | `claims_processed` `e3.py:210-214`; `site` on all three events; FK alarm present. `site` on `e3.unknown_entity_type` is hardcoded `response` (`e3.py:469-475`) — see **MINOR-3** |
| MINOR-1 — failing retry discards attempt-1 legal assertions | **Open, and now sharper** | `e3.py:435-491` still lets the attempt-2 error propagate; with the narrowed catch a *non-soft* error on attempt 2 now dead-letters the version — the opposite of design §5:110-114. See **FINDING-1** |
| MINOR-2 — unbounded illegal-type strings in prompt and logs | **Partially fixed** | `_bounded_type_label` (`e3.py:505-510`) caps each label at 48 chars in the retry prompt only; token *count* still unbounded (`e3.py:483-486`), log fields still raw `sorted(illegal)` (`e3.py:313-318`, `:396-401`, `:469-475`), and only `\n` is stripped |
| MINOR-3 — `e2_e3_claims_relations_design.md` not cross-linked | **Open** | grep: no `D86` / "type gate" / "unknown entity" match in that file |
| NIT-1 — Rule 2 framing in the accepted design | **Open** | `plan/designs/e3_unknown_entity_type_gate_design.md:201` "defer", §12 checklist `:203-209`; `plan/plans/` still has no D86 content |
| NIT-2 — mypy errors in the test file | **Open, larger** | 5 errors now (`test_e3_unknown_entity_type_gate.py:176`-ish return-value, `:210`, `:243`, `:278`, `:307` arg-type). Not CI-gating; `e3.py` and `resolver.py` are mypy-clean |
| NIT-3 — `assert response is not None` as control flow | **Open** | `e3.py:490` |
| Test gap — no `_normalize_claim` coverage | **Fixed** | `test_e3_unknown_entity_type_gate.py:233-327` (three cases) |
| Test gap — cost keys unasserted | **Fixed** | `:194-197`, `:227-230` via `RecordingCostMeter` |
| Test gap — `handle`-level isolation / N-claims invariant | **Open** | No test constructs `handle`; see **Test gaps residual** |

## Findings

### FINDING-1 (major, documentation) — the binding design now contradicts the code in three places

The code changes in this commit are the ones both round-1 reviews demanded, and
I would not want them reverted. But `plan/designs/e3_unknown_entity_type_gate_design.md`
was not amended with them, so the binding doc currently misdescribes the system:

1. **§5, `:110-114`** — "If a **retry generate raises** (provider error): treat
   like other claim-level soft failures — log, skip this claim, **continue** the
   version job (do not re-raise to DLQ the document for type-path recovery
   failures)." The code re-raises every `ProviderCallError` that is not a
   `ProviderInvalidResponseError` (`e3.py:494-502`), including on the retry
   attempt. A transport failure on attempt 2 dead-letters the version.
2. **§6, `:134-136`** — "Unexpected exceptions on a **single claim** are caught,
   logged as `e3.claim_normalize_error`, and the loop continues." The code does
   the opposite for unexpected exceptions: `_is_claim_soft_failure` returns
   `False` for anything that is not a schema failure, and they re-raise.
3. **The no-progress breaker (`e3.py:215-226`) and the `:failure` billing key
   (`e3.py:446-455`) appear nowhere in the design.** Both are new invariants —
   one decides when a version job fails, the other is cost-ledger surface area.
   §5's cost-key table (`:116-127`) lists only `a1`/`a2`.

Round-1 named this tension as *internal to the design*; the implementation
resolved it correctly in code and left the doc holding the losing side. Under
CLAUDE.md Rule 1 a stranger reading the design cold now gets the isolation
contract wrong in the direction that matters (they will believe a provider
outage mid-retry is soft). Amend §5 and §6 to state the actual rule — *only
claim-attributable structured-output poison is soft; everything else uses the
outer ledger; a whole handle that soft-fails with zero progress is an outage
class and fails* — and add the breaker and the `:failure` key to §5/§8. This is
a paragraph of editing, not a redesign.

### FINDING-2 (minor) — `e3.entity_type_fk_violation` fires on any `IntegrityError`

`e3.py:191-197` catches bare `IntegrityError` and logs it as
`e3.entity_type_fk_violation` — a signal design §8 (`:172-173`) says "should be
~zero after this design", i.e. an alarm someone will page on. Nothing inspects
the violated constraint. Any integrity failure raised anywhere inside
`_normalize_claim` is reported as the entity-type FK class: the relation upsert
path, the mention insert (`resolver.py:747`), the alias insert
(`resolver.py:733-741`, which has no `ON CONFLICT`), or a predicate-registry FK.
Several of those are unlikely in practice — the alias unique key includes the
freshly-minted `entity_id` — but "unlikely" is a weak property for an alarm
whose whole value is that a non-zero reading means the gate broke.

Related, same lines: the logged `error_class=%s` is passed
`IntegrityError.__name__` / `UnregisteredEntityTypeError.__name__` — the class
constant, not `type(exception).__name__` as the soft branch correctly uses at
`e3.py:208`. It therefore always prints the static string and discards the
actual subclass and any DB constraint detail. Cheap fix: match on the constraint
name (or `exception.orig`) for the FK alarm, log a distinct
`e3.claim_integrity_error` otherwise, and use `type(exception).__name__`
throughout.

### FINDING-3 (minor) — the breaker is all-or-nothing, so blast radius depends on document size

`e3.py:215-220` fires only when `soft_claim_errors == claims_processed` **and**
nothing at all was written. Two consequences worth recording:

- **Partial outage stays silent.** 100 claims, 95 soft-fail, 5 land → the
  conjunction is false, the version is marked `succeeded`, and 95% of the
  document's facts are missing with only per-claim log lines as evidence. Round-1
  offered a threshold *or* a zero-progress rule; the zero-progress branch alone
  leaves this hole. Once a rate for `soft_claim_errors / claims_processed` exists
  (it does now, `e3.py:210-214`), a share-based breaker is a one-line addition.
- **Small documents are treated more harshly than large ones.** A single-claim
  document whose content reliably breaks structured output has
  `claims_processed == 1 == soft_claim_errors` → the breaker fires → retry, then
  dead-letter. The identical poison inside a ten-claim document soft-drops and
  the job succeeds. The retry path makes this mostly benign (schema failures are
  often transient), and failing loudly is arguably the better half of the
  asymmetry — but it is an asymmetry the design does not mention, and E1's
  precedent for the same class is an explicit "size-1 typed skip"
  (`e1.py:511-513`). Pick one and write it down.

Also note the breaker's guard reads `not created_relations`, which counts only
*newly created* relations — a claim whose relations all already existed
contributes nothing. That makes the breaker slightly more eager, which is the
safe direction; no change needed, but it is not the same as "wrote nothing".

### FINDING-4 (minor) — failure-tier naming diverges from the repo convention, and the `:failure` metering can double-count in future

Two small things at `e3.py:446-455`:

- **Tier name.** Every other in-handler failure meter in this repo uses a
  `*_failed_response` suffix: `base.py:309` `failed_response`, `e0.py:872`
  `fallback_failed_response`, `e0.py:1037` `title_classifier_failed_response`,
  `e0_summary.py:587` `section_summary_failed_response`. E3 records
  `normalize_failed`. A cost query written as `tier LIKE '%failed_response'` —
  the natural one given four of five sites — silently misses E3. Rename to
  `normalize_failed_response`.
- **Latent double-count.** E3 meters the usage and then **re-raises**; if the
  exception is not a `ProviderInvalidResponseError` it escapes `handle` and
  `base.py:300-317` meters the *same* usage again under `provider_failure`. I
  checked the live adapter: only `OpenRouterInvalidResponseError` carries
  `usage` (`openrouter.py:238-250`, `:286-289`) and `_post` failures carry none,
  so today this cannot fire. But the invariant "meter, then re-raise" is exactly
  what makes it fire the day a transport error learns to report usage. Note that
  E0's equivalent site (`e0.py:868-880`) meters and *returns* — it never
  re-raises, so it has no such overlap. Either meter only on the soft path, or
  make the guard `if exception.usage is not None and _is_claim_soft_failure(...)`.

### FINDING-5 (minor) — round-1 MINOR-2 is only half-bounded

`_bounded_type_label` (`e3.py:505-510`) caps each token at 48 characters and
strips `\n`. Still unbounded or unescaped:

- **Count.** `e3.py:483-486` joins *every* distinct illegal token into the retry
  prompt. `EntityRef.type` has only `min_length=1`, and the number of distinct
  invented types in one response is not capped, so the suffix length is
  `O(distinct illegal types)`.
- **Logs.** `e3.py:313-318`, `:396-401` and `:469-475` all log raw
  `sorted(illegal)` / `[observation.subject.type]`, not the bounded label — so
  the log record, which is the thing the codex review asked to bound for
  cardinality, is untouched.
- **Control characters.** Only `\n` is replaced; `\r`, `\t` and ANSI escapes
  pass through into both prompt and log line.

Low likelihood, still cheap: cap at (say) 10 tokens plus an `+N more` bucket,
route every log field through `_bounded_type_label`, and widen the strip.

### MINOR-4 (docs) — the troubleshooting playbook still has no unknown-type entry

`website/src/app/docs/ingestion/pipeline/page.mdx:27` is fixed and correct. But
the page an operator opens during exactly this incident —
`website/src/app/docs/troubleshooting/page.mdx`, which has an "Ingestion stuck or
failed" section (`:50-98`) and an "Empty or weak retrieval" section (`:100`) —
says nothing about "normalize succeeded but observations are 0". That is the
observable symptom of D86's own soft-drop path, and the grep
(`e3.unknown_entity_type*`) that resolves it is documented nowhere an operator
will look. `/docs/project-status` makes no E3 normalization claim, so it is
truthful as-is.

### NIT (carried) — round-1 MINOR-3, NIT-1, NIT-2, NIT-3

Unchanged and still worth clearing: no D86 cross-link in
`e2_e3_claims_relations_design.md` (the design's own header claims to amend it);
`design:201` "defer" row and the §12 implementation checklist still in a design
doc against CLAUDE.md Rule 2, with `plan/plans/` still empty of D86; five mypy
`arg-type`/`return-value` errors in the test file from `dict` invariance on the
canned payloads (annotate them `dict[str, object]`); and `assert response is not
None` at `e3.py:490` as type-checker control flow.

## Test gaps residual

9 tests pass. The additions are the right ones — the three `_normalize_claim`
cases (`:233-327`) cover drop-before-resolve for both endpoints and the
legal-sibling property, `:294` pins that a discarded relation never reaches
`ensure_other_predicate`, and `:194-197` / `:227-230` assert the `:a1`/`:a2` cost
keys that round-1 called invisible-forever. Against design §9 (`:176-185`) and
the new code:

| Case | Covered? | Gap |
| --- | --- | --- |
| Observation `Process`, legal second response; cost keys a1+a2 | ✅ | `:154-197` |
| Observation illegal twice → dropped, no mint, job continues | Partial | `:233-264` proves the drop and zero `resolve` calls against a fake resolver; the "job continues" half is still untested (no `handle` call) |
| Relation with illegal types dropped before resolve | ✅ | `:267-296` |
| Mixed legal + illegal in one final response | ✅ | `:299-327` |
| All-legal → single generate, no retry | ❌ | No test asserts exactly one `generate` on the clean path |
| N claims, one always-illegal → other N−1 process, terminal branches enqueued, job succeeds | ❌ | **Still the D86 invariant and the BEAM regression test.** No test constructs `handle` |
| Resolver defense: mint rejects unregistered type | ❌ (in effect) | `:381-386` constructs an `UnregisteredEntityTypeError` itself and asserts its message contains `"Process"` — it never calls `_mint`, never touches `resolver.py`, and would pass if the guard were deleted. The actual guard (`resolver.py:436-446`) has zero coverage |

Two gaps that are new with this commit and matter more than the leftovers:

- **The no-progress breaker (`e3.py:215-226`) is untested.** It is the only code
  in the change that turns a successful job into a failed one, its condition is a
  four-term conjunction, and nothing exercises either polarity. A test that runs
  `handle` over N claims with an always-raising `ProviderInvalidResponseError`
  provider (expect raise) and one where a single claim still lands (expect
  success) would pin both edges.
- **The mint guard is untested against a real database.** `_E3Rig`
  (`src/tests/workers/test_e3_chain.py:185`) already composes E0→E3 against
  Postgres with the real `CascadeResolver`. One test there — resolve an
  `EntityRef` with an unregistered type, expect `UnregisteredEntityTypeError`
  and no `entities` row — would cover both the guard and its SQL. I verified the
  SQL by hand against the DDL, but hand-verification is not a regression test.
  Related: existing Postgres tests only mint core types (`test_resolver.py` uses
  `Person`/`Organization` on a bootstrapped deployment), so the new guard should
  not break CI — but those tests are skipped locally, so CI is the first real
  execution of `resolver.py:436-446`.

## Recommendation

**APPROVE_WITH_NITS.** Both round-1 blockers are fixed, and fixed in the shape
the reviews asked for rather than around them: the isolation is typed and
narrow, systemic failures reach the ledger again, the fully-failed job now fails,
usage is metered, and the mint site refuses illegal types with a typed error that
E3 turns into a loud alarm. I found no correctness defect in round 2.

Before merge (cheap, both are edits not redesigns):

1. **Amend the design** (FINDING-1) — §5 `:110-114` and §6 `:134-136` currently
   state the opposite of the shipped isolation rule, and the breaker and
   `:failure` cost key are undocumented. Binding docs should not lose to the code.
2. **Tighten the FK alarm** (FINDING-2) — match the constraint before claiming
   `e3.entity_type_fk_violation`, and use `type(exception).__name__`.

Strongly recommended, can ride the same PR or the immediate follow-up:

3. The two missing tests — `handle`-level N-claims isolation (both breaker
   polarities) and the mint refusal in `_E3Rig`. These are the two paths where a
   future refactor silently reopens the incident.
4. FINDING-3's share-based breaker (or an explicit written decision that the
   zero-progress rule is the whole rule), FINDING-4's tier rename, FINDING-5's
   log/count bounding, and MINOR-4's troubleshooting entry.

The carried nits (design cross-link, Rule 2 framing, test-file mypy, the
`assert`) are housekeeping and need not gate the merge.
