# Implementation review (round 4): D86 E3 unknown entity type gate

**Verdict:** APPROVE_WITH_NITS — *no code change required; the open items are
design-doc and test edits*
**Reviewer:** Claude Opus
**Date:** 2026-08-10

**Branch:** `fix/e3-unknown-entity-type-retry-drop` at `98a3773a`
**Fix commit under review:** `98a3773a` "fix(e3): soft-isolate normalizer generate
only (not resolve)", on top of `acf605a5`
**Prior reviews:**
[claude r3](REVIEW_claude-opus_e3_unknown_entity_type_gate_impl_r3_2026-08-10.md) (APPROVE_WITH_NITS) ·
[codex r3](REVIEW_codex-sol_e3_unknown_entity_type_gate_impl_r3_2026-08-10.md) (REQUEST_CHANGES) ·
[claude r2](REVIEW_claude-opus_e3_unknown_entity_type_gate_impl_r2_2026-08-10.md) ·
[codex r2](REVIEW_codex-sol_e3_unknown_entity_type_gate_impl_r2_2026-08-10.md)
**Binding design:** `plan/designs/e3_unknown_entity_type_gate_design.md` (D86),
`decisions.md:3316`

## Verification performed

Read the full `98a3773a` diff and the post-fix `workers/e3.py` (`handle`,
`_normalize_claim`, `_generate_normalize_response`), the whole test file, and
the amended design §5. Re-derived the billing invariant end to end rather than
reading the commit message: which exception classes can still reach
`Worker._record_failed_provider_usage` (`base.py:235`, `:300-317`), and which are
now consumed inside E3. Traced what a re-raise from `resolve` actually does to a
half-applied claim by following `FactCatalog.upsert_relation`'s transaction scope
(`fact_catalog.py:55`, `:92-101`) into the replay marker
(`entity_registry.py:128-136`, `:199-205`). Checked that the deleted
`_is_claim_soft_failure` leaves no dead references anywhere in the tree.

**Commands run.**

- `uv run pytest src/tests/workers/test_e3_unknown_entity_type_gate.py -q` →
  **12 passed** (2.27s)
- `uv run pytest src/tests/workers -q` → **101 passed, 90 skipped** (4.35s); the
  E3 chain suite (`test_e3_chain.py`) is Postgres-gated and skips locally, so CI
  is still the first execution of the integration path
- `uv run ruff check workers/e3.py` + the test file → **All checks passed**
- `uv run mypy workers/e3.py` → **no errors attributable to e3.py** (the run's 57
  errors are all in unrelated modules); the test file has **one** non-import
  error left (`:177`, `dict` invariance on the router payload), down from the
  five carried since r2

## Codex r3 major — confirmed fixed

**Major: `ProviderInvalidResponseError` from the resolver was soft-swallowed,
unbilled and possibly mid-claim.** **Fixed, in the shape codex asked for
("narrow soft isolation to the normalizer generate boundary").**

The soft class is no longer an exception-class exemption spanning
`_normalize_claim`; it is a *site*. `_generate_normalize_response` catches
`ProviderInvalidResponseError` at the provider call itself (`e3.py:456`), meters
`normalize:{claim_id}:aN:failure` when usage is present (`:460-465`), logs
`e3.claim_normalize_error … site=generate` (`:466-470`), and returns `None`
(`:471`). `_normalize_claim` converts that to a `bool` before touching anything
(`:300-308`) and `handle` counts it (`:199-202`). The handler's remaining
`except` arms (`:182-198`) both re-raise unconditionally, so nothing downstream
of generate is soft any more.

I checked the three consequences that follow, rather than assuming them:

1. **The resolver path now bills exactly once.** A `ProviderInvalidResponseError`
   from `CascadeResolver.resolve` (`resolver.py:395`, `:409` are the T4 generate
   sites) propagates out of `_normalize_claim` → out of `handle` → into
   `base.py:235`, which records `provider_failure`/`failed_response` from
   `exception.usage` (`base.py:300-317`). E3 records nothing for it, so the two
   accounting paths stay disjoint — now by *site*, which is a stronger guarantee
   than r3's by-construction argument, because it no longer depends on which
   classes happen to be raised below the generate call.
2. **The soft skip is atomic.** The `response is None` test at `e3.py:307` sits
   *before* the relation loop, `ensure_other_predicate`, every `resolve`, and
   every `upsert_relation`. A soft-skipped claim therefore writes nothing —
   which is what makes the "not marked normalized, so a replay re-derives it"
   recovery contract true for this path (`entity_registry.py:199-205`).
3. **`except ProviderCallError: raise` (`:472-475`) is not dead code.** It is
   what keeps the systemic class out of the `ProviderInvalidResponseError`
   handler now that the latter is a subclass-specific arm; the negative test
   (`test_systemic_provider_error_not_metered_in_generate`, `:442-475`) still
   passes and asserts `meter.records == []`.

The three new/rewritten tests are the right ones and each fails if the fix is
reverted: `test_generate_soft_poison_returns_none_and_meters` (`:330-364`) pins
`None` + exactly one `a1:failure` record; `test_normalize_claim_soft_skip_does_not_resolve`
(`:367-403`) pins `soft is True` **and** `resolver.calls == []` (the atomicity
above); `test_resolver_invalid_response_is_not_soft` (`:406-439`) is exactly the
regression codex asked for. Design §5 was amended to state the site rule
(`design:110-121`).

**No correctness defect found in round 4.** The code is approvable as it stands.

## Findings

### FINDING-1 (minor, design consistency) — §6 still teaches the rule §5 just replaced

§5 now reads "soft isolation applies **only at the normalizer generate
boundary** (not around resolve/upsert)" (`design:110-111`). §6 — the section
titled *Isolation (document blast radius)*, i.e. the one an implementer reads to
build this — still reads:

> **Claim-soft exceptions** are only `ProviderInvalidResponseError`; log
> `e3.claim_normalize_error` with `error_class` and continue the claim loop.
> (`design:144-145`)

That is the class-based rule, stated without a site qualifier — precisely the
formulation that produced the shipped bug codex blocked in r3. A stranger
implementing from §6 alone re-introduces it. Under CLAUDE.md Rule 1 the binding
doc has to be correct read cold, and §6 currently is not. It is a one-line edit:
"only `ProviderInvalidResponseError` **raised by the normalizer generate call**;
the same class from resolve/upsert re-raises." Same PR.

Related one-liner in the same pass: §8's event table lists
`e3.claim_normalize_error` with fields `claim_id`, `error_class`
(`design:178`); the implementation now also emits `site=generate` (`e3.py:467`),
which is the field that distinguishes this event from the boundary the design
spent two rounds narrowing.

### FINDING-2 (minor, design completeness) — the re-raise fixes the billing, but the failing claim stays permanently partial

Codex's r3 ask was "narrow soft isolation **or** explicitly define and meter
resolver-invalid-response isolation with call-specific keys and partial-claim
semantics". The first branch was taken, which is the right call — but the
partial-claim behaviour codex named still exists on the re-raise path, and it is
sharper than it looks. I verified the chain:

1. `upsert_relation` opens its **own** transaction per call
   (`fact_catalog.py:55`) and inserts `relation_evidence` keyed by `claim_id` in
   it (`:92-101`). So relation *k* of claim C is committed before relation *k+1*
   is attempted.
2. A `resolve` failure on relation *k+1* (`e3.py:350-365`) now re-raises, and
   `Worker.run_one` schedules a retry (`base.py:243-252`).
3. On the retry, `handle` re-reads `normalized_claim_ids`
   (`e3.py:158-160`), which is **evidence-backed** — `relation_evidence ∪
   observation_evidence` (`entity_registry.py:199-205`). Claim C now has an
   evidence row, so it is skipped as a replay (`e3.py:166-167`).

Net: the retry recovers every *other* claim in the version, but relation *k+1*
onwards and **all** of claim C's observations (they are batched at
`e3.py:221-228` and were never reached) are never re-derived. The same marker
that makes the *soft* path recoverable makes the *systemic* path lossy.

This is **pre-existing** — it holds for any mid-claim DB error or transport
failure and is not introduced by `98a3773a` — and the alternative this commit
replaced was strictly worse (same loss, plus unbilled). So it is not a blocker.
But it is now the designated resolver-failure path, and §6 is the place it has
to be written down: one sentence saying that a systemic failure mid-claim leaves
that claim partially applied and that the evidence-backed replay marker will not
re-derive the remainder, so the residue is recoverable only by re-extraction.
Silently inheriting it is what Rule 1 forbids; stating it as a documented
boundary is fine.

### FINDING-3 (minor, carried from r3 FINDING-2, now cheaper to fix) — a hard failure on the retry still discards attempt 1's legal facts

`_generate_normalize_response` holds the last successful response in `response`
(`e3.py:444`, assigned `:477`). Two exhaustion paths, two different outcomes:

- attempt 2 returns a *parseable but still illegal* response → `break` →
  `return response` (`:496-497`, `:510-511`) → the gates drop only the
  illegal-bearing assertions and keep the legal siblings (pinned by
  `test_normalize_claim_keeps_legal_sibling_observation`, `:299-327`);
- attempt 2 *raises* `ProviderInvalidResponseError` → `return None` (`:471`) →
  the whole claim is skipped, including attempt 1's legal relations and
  observations, which are sitting unused in `response`.

r4 changed this from "raise" to "return None", so the asymmetry now ends in a
silent soft skip rather than a dead letter — same observation as r3, one notch
more worth closing. The fix is two lines and needs no new control flow:
`if response is not None: return response` before the `return None`, i.e. fall
back to the last good response and let the deterministic gates do their job.
Whichever way it is decided, §5 should state which response is used when the
retry itself fails — it currently only covers the parseable case
(`design:107-109`).

## Carried r3 items — disposition after `98a3773a`

`98a3773a` touched `e3.py`, the test file, and design §5 only, so everything
below is unchanged. None of it blocks the code; the first two are the r3
"before merge" doc asks.

| r3 item | Status | Evidence |
| --- | --- | --- |
| **r3 FINDING-1** — register `e3.normalize_all_soft_failed` in §8 as an alarm-grade event; state the replay contract in §7 | **Open** | §8's table (`design:172-178`) and its FK-alarm paragraph (`:183-184`) still omit it; §7 (`:154-163`) still does not state that soft-failed claims are not marked normalized. FINDING-2 above adds the counterpart sentence for the systemic path |
| **r3 FINDING-4 / D66 same-PR docs** — pipeline page's soft-drop list | **Open** | `website/src/app/docs/ingestion/pipeline/page.mdx:27` still lists only unknown predicates, signature rejects, and post-budget unknown types; claim-level structured-output poison (a drop inside a *successful* stage, up to a zero-fact version) is absent. `troubleshooting/page.mdx` still has no `e3.unknown_entity_type*` / `e3.normalize_all_soft_failed` entry |
| r3 FINDING-3 — FK classifier's constraint-name branch cannot match the real (unnamed → `entities_deployment_id_type_fkey`) constraint | **Open** | `e3.py:514-524`; the message route still works, so this only risks silencing the alarm |
| r3 NIT-1 — illegal-label *count* unbounded (per-label width is capped) | **Open** | `e3.py:503-509`; `\r`/`\t` still pass through `_bounded_type_label` (`:529`) |
| r3 NIT-2 — mypy errors in the test file | **Improved** | one left (`:177`, `return-value` invariance), was five |
| r3 NIT-3 — `assert response is not None` as control flow | **Open** | `e3.py:510`; under `python -O` the assert vanishes and `None` reaches `_normalize_claim`. Note this is now *survivable* — `:307` handles `None` — so it degrades to a silent soft skip instead of an `AttributeError`, which is arguably worse for diagnosis and argues for FINDING-3's explicit fallback |
| r3 NIT-4 — one-element list comprehension | **Open** | `e3.py:400-404` |
| r3 NIT-5 — doc housekeeping: no D86 cross-link in `e2_e3_claims_relations_design.md`; §12 checklist and the "defer" row are build-sequencing content (CLAUDE.md Rule 2) with no `plan/plans/` entry | **Open** | `design:210` ("Larger change; defer"), `design:212-219`; `grep -rl D86 plan/plans` → empty. `decisions.md:3333` also carries "Per-claim work-ledger fan-out (deferred)" |

## Test gaps residual

12 tests pass and the round-4 additions are well-targeted (each pins a distinct
half of the new boundary, and `:367-403` doubles as the atomicity proof). Two
gaps survive, both named by codex r3 and by my r3:

- **No test constructs `handle`.** Third round running. This is now the only
  untested decision that determines whether a version job goes green: the
  all-soft branch (`e3.py:208-220`), the N−1-claims-continue invariant (design
  §9, `:195`), the terminal follow-ups on the all-soft path, and the
  "systemic error mid-loop escapes `handle`" contract that r4 just made
  load-bearing. One test with a fake claim/chunk catalog and a scripted provider
  closes all four. Worth doing in this PR, since r4 is precisely a change to what
  escapes `handle` and nothing at that level regression-protects it.
- **`a2:failure` is unmetered in tests.** Both soft-poison tests raise on every
  attempt, so they exercise `a1:failure` only (`:330-364`, `:367-403`). The
  D86-specific case — illegal-but-parseable `a1`, usage-bearing invalid `a2` —
  should assert exactly `[a1, a2:failure]`. It is also the case FINDING-3 is
  about, so one test covers both.

## Recommendation

**APPROVE_WITH_NITS.** Codex's r3 major is genuinely fixed, and fixed in the
better of the two shapes codex offered: soft isolation is now a *site* (the
normalizer generate call), not an exception class spanning the claim, so
resolver structured-output failures bill exactly once through
`base.py:300-317` and can no longer soft-succeed a claim. I re-derived the
billing disjointness, the atomicity of the soft skip, and the systemic
re-raise path rather than trusting the tests, and found no correctness defect
this round. `ruff` and `mypy` are clean on `e3.py`; 12/12 and 101/101 pass.

Nothing here blocks merging the code. Two doc edits should ride along in the
same PR:

1. **FINDING-1** — the §6 one-liner. The binding design's isolation section
   still states the class-based rule that caused this round; leaving it is how
   the fix gets undone by the next reader.
2. **r3 FINDING-1 + FINDING-2** — one sentence each in §7/§8: register
   `e3.normalize_all_soft_failed` as an alarm-grade event, and state the
   two-sided replay contract (soft-skipped claims re-derive on replay because
   nothing was written; a systemic failure mid-claim does *not*, because the
   marker is evidence-backed).

Strongly recommended, same PR or immediate follow-up: the `handle`-level test
(third request), the `a2:failure` metering test, **FINDING-3**'s fallback to the
last good response, and the D66 pipeline-page clause. r3 FINDING-3 and NIT-1..5
remain housekeeping and need not gate the merge.
