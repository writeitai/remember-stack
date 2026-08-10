# Implementation review: D86 E3 unknown entity type gate

**Verdict:** REQUEST_CHANGES
**Reviewer:** Claude Opus
**Date:** 2026-08-10

**Branch:** `fix/e3-unknown-entity-type-retry-drop` (`993f989e`) vs `origin/main`
**Under review:** `src/rememberstack/workers/e3.py`,
`src/tests/workers/test_e3_unknown_entity_type_gate.py`
**Binding design:** `plan/designs/e3_unknown_entity_type_gate_design.md` (D86),
`plan/analysis/e3_unknown_entity_type_gate_analysis.md`, `decisions.md` D86,
and the two design reviews dated 2026-08-10.

**Verification performed:** read the full pre- and post-change `e3.py`; traced the
mint path through `spine/resolver.py`; traced exception handling through
`workers/base.py`; checked cost-key semantics against `spine/work_ledger.py`;
grepped every `E3_NORMALIZER_VERSION` consumer; ran the new test file
(3 passed), `ruff check` (clean), `ruff format --check` (clean), `mypy` on the
changed files, and `test_e3_chain.py` (6 skipped — no Postgres locally; these
run in CI job `.github/workflows/ci.yml:164`).

## Summary

The gate itself is right. The detection helpers, the placement of both gates
before `resolve`, whole-response replacement, the `:aN` cost keys, temperature 0,
and the version bump all match the accepted design, and the relation gate is
correctly ordered *ahead* of `ensure_other_predicate` so a discarded response
cannot pollute the predicate registry. If a `Process` observation arrives today,
it is retried once and then dropped, and no illegal type reaches
`_INSERT_ENTITY`. The BEAM incident, as it happened, is fixed.

What blocks approval is not the gate — it is the isolation mechanism wrapped
around it and the parts of D86 that were not built.

`workers/e3.py:162-180` wraps each claim in a bare `except Exception`. That is
broader than the failure class D86 asks it to absorb, and it silently converts
three things into "job succeeded": (a) systemic failures — a Postgres outage or
a provider outage now yields a document version whose normalize row is
`succeeded` with zero relations and zero observations, permanently, because the
work row never retries; (b) usage-bearing provider errors, whose tokens were
previously metered by `workers/base.py:235` and now are not metered anywhere;
(c) the FK `IntegrityError` itself, which the design designates as the
last-resort alarm (§8 `e3.entity_type_fk_violation`) — an alarm that is not
implemented and, behind this catch, could never fire loudly anyway. Design §5
explicitly promises the opposite behavior ("Systemic provider outages still fail
via outer ledger when the whole handle cannot progress … existing behavior");
that promise is now false.

Three design deliverables are simply absent: the `CascadeResolver` mint refusal
(§3 table, §9 test row 7 — the **#1 required change in both design reviews**),
the `claims_processed` denominator without which D86's "track rates" cannot be
computed, and the D66 same-PR docs update that design §10 names explicitly.

The tests do not touch the incident path. All three exercise
`_generate_normalize_response` and a pure helper with `resolver=None`,
`facts=None`; none call `_normalize_claim` or `handle`, so no test covers the
drop filters, the cost keys, or the "one bad claim cannot dead-letter the
version" invariant that is the entire point of D86.

## Checklist vs design

| # | Design requirement | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Drop, never coerce | ✅ | `e3.py:266-273`, `e3.py:349-356` — `continue`, no type rewrite anywhere |
| 2 | `MAX_INNER = 2` (first + one retry) | ✅ | `e3.py:56`, loop bound `e3.py:389`, break `e3.py:419-420` |
| 3 | `call_key = normalize:{id}:aN` | ✅ | `e3.py:397`; distinct per attempt, recorded *before* the legality check so a junk retry is still billed |
| 4 | Full response replacement | ✅ | `_generate_normalize_response` returns one response; no merge (`e3.py:377-430`) |
| 5 | `allowed_types` from `entity_type_parents` | ✅ | `e3.py:152` → `e3.py:158`; source is `fact_catalog.py:377-383`, the unfiltered FK row set |
| 6 | Gate relations **and** observations before resolve/mint | ✅ | relations `e3.py:262-273` (before `ensure_other_predicate` at `:277`); observations `e3.py:348-356` (before `resolve` at `:357`) |
| 7 | Claim isolation: one claim cannot DLQ the version | ⚠️ | `e3.py:162-180` — achieved, but over-broadly; see **BLOCKER-1** |
| 8 | `E3_NORMALIZER_VERSION` bump | ✅ | `e3.py:49` `…2026.08a:temp0-1:unknown-type-gate-1`; all consumers reference the symbol, no hardcoded old strings |
| 9 | Temperature 0 on both attempts | ✅ | `e3.py:392` (single request builder used by every attempt) |
| 10 | Metrics log events | ⚠️ | 4 of 5 events present; `site` field missing, `claims_processed` denominator missing, `e3.entity_type_fk_violation` missing — **MAJOR-2** |
| 11 | `CascadeResolver` mint refuses unregistered type (§3 table) | ❌ | No `entity_types` check anywhere in `resolver.py`; `_INSERT_ENTITY` executes on `reference.type` unguarded (`resolver.py:440`) — **BLOCKER-2** |
| 12 | Docs updated (§10, D66) | ❌ | No `website/` change in the diff; `docs/ingestion/pipeline/page.mdx:27` still says "never silent skip" — **MAJOR-1** |
| 13 | Test table §9 (7 cases) | ❌ | ~2 partially covered — see **Test gaps** |

## Findings

### BLOCKER-1 — `except Exception` around the whole claim turns systemic failure into silent success and drops usage-bearing spend

`src/rememberstack/workers/e3.py:162-180`

```python
try:
    self._normalize_claim(...)
except Exception:
    _logger.exception("e3.claim_normalize_error claim_id=%s", claim.claim_id)
```

The catch spans the entire claim: both generate attempts, `meter.record`, both
`resolver.resolve` calls, `ensure_other_predicate`, and `upsert_relation`.
Nothing after it can distinguish "the model invented a type" from "Postgres is
down". `handle` then always reaches `e3.py:189-193` and returns a successful
`HandlerOutcome` with both terminal branches.

**Failure scenario A (silent permanent data loss).** The provider is returning
5xx, or Postgres is unreachable, during a 15k-claim version job. Every claim
raises; every exception is logged and swallowed; `observations_by_entity` is
empty so the adjudicator loop at `e3.py:181-188` is a no-op; `handle` returns
success. The work-ledger row goes `succeeded`, `ADJUDICATE_SUPERSESSION` and
`EMBED_CLAIM` enqueue, readiness reports the version normalized. Because the
processing row succeeded, **nothing ever retries it** — and the claim-level
replay marker (`entity_registry.py:128-136`) is evidence-backed, so there is no
record that these claims were never normalized. The document permanently has
zero relations and zero observations, discoverable only by log grep. Before this
PR the same outage dead-lettered loudly and was replayable. This is a worse
failure mode than the incident D86 fixes.

**Failure scenario B (silent billing gap).** `ProviderInvalidResponseError`
(`model/model_provider.py:50-51`) is a `ProviderCallError` that carries
`usage` — a structured-output schema failure after the provider already billed
tokens. Previously it escaped `handle`, and `workers/base.py:233-235` →
`_record_failed_provider_usage` (`base.py:300-317`) metered it as
`call_key="provider_failure"`, `tier="failed_response"`. It is now swallowed
inside `handle`, so that call's tokens and cost reach **no** ledger row. This is
exactly the class of silent unbilled spend that design review P0-2 raised about
retry keys — reintroduced through a different door, and CLAUDE.md Rule 3 names
budgets as always-in-repo correctness machinery.

**Faithfulness note.** The design is internally inconsistent here: §6 authorizes
catching "unexpected exceptions on a **single claim**", while §5 requires that
"systemic provider outages still fail via outer ledger when the whole handle
cannot progress … existing behavior", and codex design review P1.1 required
that "database outages, unrelated integrity violations, and other systemic
failures must still escape". The implementation resolved that tension in the
direction that loses data, and did so without any signal — there is no counter,
no summary line, and no ceiling on how many claims may fail before the job is
still called a success.

**Shape of the fix** (not applied — review is read-only): keep the catch narrow
(the recovery-path provider error D86 §5 actually names, plus any typed
unknown-type soft error), and add the systemic escape §5 promises — e.g. count
swallowed claim errors in the job and re-raise if the failed share crosses a
threshold or if *zero* claims produced facts while ≥1 error was swallowed. Either
way, re-meter usage-bearing `ProviderCallError` before swallowing it, and emit a
per-job error count so a fully-failed job is not indistinguishable from a clean
one.

### BLOCKER-2 — The `CascadeResolver` mint refusal is missing entirely

Design §3's implementation-contract table assigns `CascadeResolver` mint:
"Refuse mint if type ∉ registry (typed error); never insert illegal type". §9's
test table has "Resolver defense | Mint path rejects unregistered type if
called". Both design reviews put this at #1 of their required changes
(opus P1-1, codex P1.1) — it is the reason the design was rewritten to name
`CascadeResolver` instead of `EntityRegistry.resolve_t0`.

It is not implemented. `git diff origin/main...HEAD` touches no file under
`spine/`. `_mint` (`spine/resolver.py:419-448`) executes `_INSERT_ENTITY`
(`resolver.py:440`, SQL at `resolver.py:698`) with `reference.type` passed
straight through, and neither `resolver.py` nor `entity_registry.py` contains a
single reference to `entity_types`. The FK remains the only stop at the mint
site.

This matters beyond bookkeeping, in three concrete ways:

1. **TOCTOU.** `allowed_types` is snapshotted once per job (`e3.py:152-158`). If
   an `entity_types` row is deleted mid-job, the gate passes a type the FK will
   reject. The design's answer to that window *is* the resolver guard.
2. **It compounds BLOCKER-1.** The FK `IntegrityError` is now caught by the
   blanket handler and logged as a generic `e3.claim_normalize_error`. The
   design's §8 FK alarm (`e3.entity_type_fk_violation`, "should be ~zero after
   this design") is also not implemented. So if the gate is ever bypassed, the
   BEAM incident class does not dead-letter loudly — it becomes another silent
   drop. The system loses the property that "the gate broke" is detectable.
3. **Defense-in-depth is the stated purpose.** Design §2 says gating both
   endpoints exists "so neither path can mint illegal types". With no guard at
   the mint site, the only thing standing between a future E3 refactor and the
   original incident is the two `continue`s in `_normalize_claim`.

Answering the review question directly: **yes, `CascadeResolver` still allows
illegal types if the gate is skipped**, and no, the refuse-mint requirement was
neither implemented nor recorded as deferred anywhere in the branch.

### MAJOR-1 — D66 same-PR docs obligation unmet; a shipped docs page is now false

Design §10 is explicit: "Update ingestion pipeline docs that claim 'never silent
skip'". CLAUDE.md makes this a standing obligation, not a suggestion.

`website/src/app/docs/ingestion/pipeline/page.mdx:27` still reads:

> Workers use the deployment work ledger: retry, then dead-letter — never silent skip.

After this change, normalize *does* drop assertions without dead-lettering, and
(per BLOCKER-1) can drop an entire document's worth of them. The diff contains
no `website/` change at all — the pipeline page, the troubleshooting playbook
(the page an operator opens for exactly this incident), and `/docs/project-status`
are all untouched. A reader running what the docs say will be wrong about
observable behavior on `main`.

### MAJOR-2 — "Track rates" is not achievable from what is emitted

`decisions.md` D86 commits to "Track unknown-type rates"; design §8 specifies the
events and their denominators. Three gaps:

- **No `claims_processed`.** §8: "Denominators (for rate queries):
  `claims_processed` per version job (log once per job with count)". Nothing in
  `handle` emits it (`e3.py:122-193`). Every rate D86 promises —
  incident-claim rate, recovery rate, residual drop rate — has a numerator and
  no denominator. The cost ledger gives normalize-call counts, but the
  per-claim denominator has to be reconstructed by counting log lines, and drops
  produce one line per *assertion*, not per claim.
- **No `site` field.** §8 requires `e3.unknown_entity_type` to carry
  `site=relation|observation|response`. `e3.py:413-418` emits only `claim_id`,
  `attempt`, `illegal_types`. Since the whole point of the corrected design §2
  is that observations were the live FK path and relations were already
  fail-closed, losing the site split loses the one dimension that tells an
  operator whether the incident class has recurred.
- **No `e3.entity_type_fk_violation`.** §8's FK alarm is unimplemented (see
  BLOCKER-2).

Not a finding, but worth recording as verified: the `:aN` call keys do make
retry spend separable from the ledger (`call_key LIKE '%:a2'`), so the separate
`tier="normalize_retry"` the opus design review suggested is not strictly needed.

### MINOR-1 — A failing retry discards attempt 1's legal assertions

`e3.py:377-430` lets an exception from the second `generate` propagate out of
`_generate_normalize_response`, so the whole claim is lost to the handler's catch
— including the legal relations and observations attempt 1 produced, which the
drop filters would have kept. Design §5 does say "log, skip this claim,
continue", so this is faithful; but it is a recall cost the design did not
quantify, and it is avoidable: retaining the attempt-1 response as the fallback
and letting the normal drop filters run over it is strictly better and no more
complex. Worth a decision recorded in the doc either way.

Related: because the retry failure is logged as `e3.claim_normalize_error`
(the same event as a genuine code bug), the design review's requested
`retry_failed` signal does not exist even implicitly. The two have different
remedies and are currently indistinguishable.

### MINOR-2 — Illegal type strings are echoed unbounded into the retry prompt and logs

`e3.py:426-428` interpolates `", ".join(sorted(illegal))` into the retry prompt;
`e3.py:413-418` and `e3.py:267-272` log the same strings. `EntityRef.type` is
constrained only to `min_length=1` (`model/relations.py:11-19`) — no maximum
length, and the number of distinct illegal tokens in one response is unbounded.
A pathological or adversarial response (document content does influence the
model output) can therefore inflate the retry prompt and the log record without
limit. Codex design review P2.3 asked for a cap on count and rendered length plus
control-character escaping; neither is present. Low likelihood, cheap to bound.

### MINOR-3 — `e2_e3_claims_relations_design.md` is not amended or cross-linked

The new design's header says "**Amends:** E3 gates in
`e2_e3_claims_relations_design.md`", but that document contains no reference to
D86 or the type gate (grep: no match). A cold reader who opens the primary E3
design — the obvious entry point — learns nothing about the gate that now runs
first in `_normalize_claim`. CLAUDE.md Rule 1 asks that a stranger be able to
read the corpus cold; a one-way link does not deliver that.

### NIT-1 — Rule 2 framing survives in the accepted design

`plan/designs/e3_unknown_entity_type_gate_design.md:201` — "Per-claim work-ledger
fan-out | Larger change; **defer**" — and §12 "Implementation checklist"
(`:203-209`) are the deferral-and-sequencing framing CLAUDE.md Rule 2 rules out
of design docs; the opus design review's P2-3 asked for the checklist to move to
`plan/plans/`, and `plan/plans/` has no D86 content (grep: no match). Recast the
fan-out row as a documented alternative and move the checklist.

### NIT-2 — Test file has mypy errors

`src/tests/workers/test_e3_unknown_entity_type_gate.py:84` and `:111` — dict
invariance on the canned payloads (`dict[str, list[dict[str, Collection[str]]]]`
vs `dict[str, object]`). CI does not gate on mypy (`ci.yml` runs `ruff` and
`pytest` only), and `ruff check`/`ruff format --check` are clean, so this is
cosmetic — annotating the payload literals as `dict[str, object]` clears it.

### NIT-3 — `assert response is not None` as control flow

`e3.py:429`. The loop structure does guarantee non-`None` (the range is
non-empty), and the assert is there for the type checker, but `python -O` strips
it and the function would then return `None` against its declared type.
Restructuring so the last-attempt response is returned from inside the loop
removes the need for it.

## Test gaps

`src/tests/workers/test_e3_unknown_entity_type_gate.py` passes (3 tests, 0.9s)
and its three cases are genuinely useful: helper detection, retry-then-legal with
the suffix asserted in prompt 2, and budget exhaustion returning the last
response. But all three construct the handler with `resolver=None`, `facts=None`,
`registry=None` (`:30-40`) and call `_generate_normalize_response` or a module
helper directly. **No test calls `_normalize_claim` or `handle`.** Everything the
gate actually does to the pipeline is untested.

Against design §9's seven cases:

| §9 case | Covered? | Gap |
| --- | --- | --- |
| Observation `Process`, legal second response — "no exception, cost keys a1+a2" | Partial | Retry mechanics covered (`:62-98`); the meter is `NoopCostMeter`, so **no test asserts the `:a1`/`:a2` keys at all** — the exact regression design review P0-2 called invisible-forever |
| Observation illegal twice → dropped, no `Process` entity minted, job continues | ❌ | `:101-125` proves only that the *response* is returned still-illegal. Nothing asserts the observation is dropped, that `resolve` is never called, or that no `entities` row appears |
| Relation with illegal types dropped before resolve | ❌ | Untested |
| Mixed legal + illegal in one final response | ❌ | Untested — the "legal siblings still land" property has no coverage |
| All-legal → single generate, no retry | ❌ | No test asserts exactly one call on the clean path |
| N claims, one always-illegal → other N−1 process, terminal branches enqueued, job succeeds | ❌ | **This is the D86 invariant and the BEAM regression test.** Untested |
| Resolver defense: mint rejects unregistered type | ❌ | Unimplemented (BLOCKER-2), so untestable |

Two more, from the design reviews' residual lists, also missing: no test that a
discarded attempt-1 `other:` predicate never reaches `ensure_other_predicate`
(the ordering at `e3.py:262-279` is correct by construction but unpinned — a
future refactor moving the type gate below the predicate funnel would silently
reintroduce registry pollution), and no test for the retry-raises path.

**The harness already exists.** `_E3Rig` (`src/tests/workers/test_e3_chain.py:185`)
composes E0→E3 against a real Postgres with the real `CascadeResolver`, and
`test_empty_document_completes_the_same_terminal_pipeline_without_model_calls`
(`:436-476`) already asserts `processing_state` stage/status rows — precisely the
assertion the "job succeeds, both terminal branches present" test needs. And
`FakeModelProvider` already supports `generate_router` (per-call payloads,
`adapters/testing/model_provider.py:41-44`), which is what attempt-1-illegal /
attempt-2-legal requires. The two highest-value missing tests — illegal
observation through the real resolver with no `entities` row minted and
`NORMALIZE_RELATIONS` = `succeeded`, and the N-claims isolation case — are cheap
to write in that rig.

## Residual risks

1. **No per-job retry breaker.** The accepted design dropped the opus review's
   P1-5 breaker, so a prompt or model regression that pushes the unknown-type
   rate high doubles LLM calls and wall-clock across an entire 15k-claim job with
   no ceiling and no alarm. Doubling the runtime of a version-scoped job also
   pushes against the work-ledger lease; a lease expiry mid-job means duplicate
   execution, and terminally-dropped claims leave no evidence marker
   (`entity_registry.py:128-136`), so the duplicate runner re-normalizes and
   re-pays exactly those claims. Not an implementation defect — but with MAJOR-2
   unresolved there is no metric that would reveal it either.
2. **Every replay re-pays the drops.** A claim whose assertions are all dropped
   writes no `relation_evidence`/`observation_evidence` row, so
   `normalized_claim_ids` never short-circuits it. Each outer replay or version
   bump pays its two generates again. Known (codex P2.4 / opus P2-8) and
   accepted; worth stating in the docs update from MAJOR-1 so operators are not
   surprised by replay cost.
3. **Deprecated registry types are accepted.** `entity_type_parents`
   (`fact_catalog.py:377-383`) returns every row with no status predicate, so a
   deprecated pack type passes the gate and stays in the prompt. This matches
   design §4 exactly (the allow-list is the FK row set, which is what makes the
   gate provably FK-safe), so it is correct as built — but the codex review's
   P2.2 question is unanswered in the design and will resurface the first time a
   pack is deprecated.
4. **Replay provenance is mixed.** Design §7 says to bump the version *and* to
   replay the existing BEAM dead-lettered row. That row carries
   `component_version = e3-normalize-2026.07b:…`, while relations written during
   the replay stamp `normalizer_version = E3_NORMALIZER_VERSION` (now `08a`).
   The audit trail will show new-gate facts under an old-version processing row.
   Operationally harmless; worth one sentence in the replay runbook so it is not
   read as corruption later.
5. **Partial-claim writes on the swallowed path.** If a claim raises after some
   relations were upserted, the committed evidence makes that claim
   short-circuit on any future replay, so its remaining observations are never
   produced. Pre-existing in shape, but BLOCKER-1 makes it silent and permanent
   rather than loud and retried.
6. **Version-bump blast radius: verified safe.** Every consumer references the
   symbol (`workers/e1.py:636`, `workers/e2.py:322`, `workers/__init__.py:32`);
   no hardcoded `e3-normalize-2026.*` string exists outside `e3.py`; the constant
   is used only to enqueue new normalize work at the chunk barrier
   (`work_ledger.py:304`), not in any readiness back-join. Already-succeeded rows
   stay succeeded and nothing mass-re-enqueues.

## Recommendation

**REQUEST_CHANGES.** The gate is correct and faithful; the surrounding contract
is not yet what D86 accepted. Required before merge:

1. **Narrow the claim catch and restore the systemic escape** (BLOCKER-1). Catch
   the soft class D86 §5 names, re-meter usage-bearing `ProviderCallError`
   before swallowing, and make a job in which every claim failed fail rather than
   succeed. Add the swallowed-error count to the per-job summary.
2. **Implement the `CascadeResolver` mint refusal** (BLOCKER-2) — a registry
   membership check immediately before `_INSERT_ENTITY` (`resolver.py:440`)
   raising a typed error, plus the `e3.entity_type_fk_violation` alarm from §8.
   This was the #1 required change in both design reviews and is the only thing
   that makes "the gate broke" detectable.
3. **Update `website/`** (MAJOR-1): the "never silent skip" sentence at
   `docs/ingestion/pipeline/page.mdx:27` must distinguish job-level dead-letter
   from assertion-level re-derivable drops; add the unknown-type outcome to the
   troubleshooting playbook; keep `/docs/project-status` truthful.
4. **Emit the denominator and the `site` field** (MAJOR-2) so D86's rate
   commitment is computable.
5. **Add the two incident-class tests in `_E3Rig`** and the cost-key assertion:
   illegal observation → no `entities` row, normalize `succeeded`; N claims with
   one always-illegal → other claims land, both terminal branches enqueue; a
   retried claim records two ledger rows with distinct `:a1`/`:a2` keys.

Items 1, 2 and 5 are the ones that decide whether the BEAM Process FK incident
can recur undetected. Items 3 and 4 are standing obligations the branch has not
met. The minors and nits can ride along or follow.
