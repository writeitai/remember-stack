# Review — D85 E3 unknown entity type gate (retry then drop)

- **Reviewer:** claude-opus (Opus 5)
- **Date:** 2026-08-10
- **Scope:** `plan/designs/e3_unknown_entity_type_gate_design.md`,
  `plan/analysis/e3_unknown_entity_type_gate_analysis.md`, `decisions.md` D85
- **Cross-read for verification:** `workers/e3.py` (`_normalize_claim`,
  `_signature_allows`), `spine/entity_registry.py` (`resolve_t0`),
  `spine/resolver.py` (`CascadeResolver.resolve`, `_mint`),
  `spine/fact_catalog.py` (`entity_type_parents`), `spine/work_ledger.py`
  (`record_call`), `workers/base.py` (`_LedgerCostMeter`), `workers/e1.py`
  (normalize enqueue), `website/src/app/docs/{ingestion/pipeline,troubleshooting}`

## Verdict

### **Request changes**

The **policy is right** and should stand: retry-then-drop beats coerce, and it beats
the status quo by a wide margin. The **document is not yet implementable as written**.
Two defects would ship if an implementer followed it literally (a duplicate decision
number in the canonical log; silently unbilled retry tokens), and the design
misidentifies both the code path that caused the incident and the class that performs
the mint — so its "defense in depth" lands in a function E3 never calls. A cold reader
following §5/§6 would build the gate in the wrong place and measure the wrong denominator.

None of this is fatal to the approach. All findings are local edits to the design plus
one added mechanism (a job-level retry breaker).

## Findings

| ID | Sev | Title |
| --- | --- | --- |
| P0-1 | P0 | `decisions.md` now has **two** D85 entries; the LoCoMo D85 is already cross-referenced |
| P0-2 | P0 | Retry reuses the cost-meter `call_key` — retry tokens are silently dropped from billing |
| P1-1 | P1 | Wrong mint path named: E3 mints via `CascadeResolver._mint`, not `EntityRegistry.resolve_t0` |
| P1-2 | P1 | The design never states which path actually FK'd — relations already fail closed; observations do not |
| P1-3 | P1 | Retry trigger is broader than the failure population; the cost model uses an unmeasured denominator |
| P1-4 | P1 | At temperature 0 the retry only works because the prompt changes — unstated, and the suffix is not normative |
| P1-5 | P1 | No job-level retry breaker: a prompt/model regression doubles cost and wall-clock silently |
| P1-6 | P1 | Retry-response substitution unspecified — a thinner retry silently discards attempt-1 legal relations |
| P1-7 | P1 | Retry-call failure path unspecified — an exception on the *recovery* call re-creates the bug class |
| P1-8 | P1 | Over-drop on the registry-hit path is real and undocumented |
| P1-9 | P1 | `E3_NORMALIZER_VERSION` unaddressed — provenance and the §12.4 replay plan both depend on it |
| P1-10 | P1 | Unbounded metric label cardinality (`illegal_types[]`, `claim_id`) |
| P1-11 | P1 | D66 same-PR docs obligation missing; `/docs/ingestion/pipeline` currently says the opposite |
| P2-1 | P2 | Rule 2 framing violations: "Constants (v1)", "future proposal", "defer", "(deferred)" |
| P2-2 | P2 | Status line self-contradicts ("accepted … pending review") |
| P2-3 | P2 | §12 implementation checklist is sequencing — belongs in `plan/plans/` |
| P2-4 | P2 | Health bands mix denominators; no band on the event rate itself |
| P2-5 | P2 | Strict exact match burns an LLM call on case/whitespace variants |
| P2-6 | P2 | Gate reordering silently changes the meaning of existing `signature-rejected` logs |
| P2-7 | P2 | Per-event warning logging on a 15k-claim job |
| P2-8 | P2 | No durable record of drops; each replay re-pays the retry |

---

## P0

### P0-1 — Duplicate decision number

`decisions.md:13` adds `## D85. E3 unknown entity types: retry then drop`.
`decisions.md:3303` already holds `## D85. The full-system LoCoMo answer seat gets the
complete shipped read plane` (2026-08-07, commit `0a23730e`), and it is cited outside the
log at `plan/designs/locomo_benchmark_design.md:64` ("decision: D85"). The log is the
canonical record; two live entries under one ID makes every future `D85` reference ambiguous.

The new entry was also **inserted at the top of the file** (line 13, immediately after the
preamble), whereas every other entry D1…D85 is in append order.

**Fix.** Renumber the E3 entry to **D86**, move it to the end of the log, and update
`plan/designs/e3_unknown_entity_type_gate_design.md`,
`plan/analysis/e3_unknown_entity_type_gate_analysis.md`, and `design/README.md` to cite D86.
Leave the LoCoMo D85 untouched — it is the one already referenced externally. The commit
message `design(e3): … (D85)` cannot be corrected in place; note the renumber in the PR body.

### P0-2 — The retry's tokens will not be billed

`WorkLedger.record_call` is **idempotent per call key** and documents it:

> "Returns False when the (processing, attempt, call_key) row already exists,
> so an acknowledged-late retry cannot double-bill." — `spine/work_ledger.py:466-473`

`_normalize_claim` records with `call_key=f"normalize:{claim.claim_id}"` (`workers/e3.py:238`).
A second normalize call for the same claim inside the same attempt therefore hits an existing
`(processing_id, attempt, call_key)` row and is **silently discarded** — the retry's tokens,
cost, and latency never reach the spend ledger. The design mandates that second call and never
mentions the key.

This is P0 rather than P1 because budgets are named in CLAUDE.md Rule 3 as machinery that is
always fully in-repo and correctness-determining. Under the P1-5 regression scenario (every
claim retries), half of normalize spend becomes invisible to budget enforcement.

**Fix.** §5 and §6 must specify the key explicitly:
`call_key=f"normalize:{claim_id}:attempt{n}"` with `n` 1-based, and a distinct meter tier for
retry calls (`tier="normalize_retry"`) so retry spend is separable in cost reporting without a
log join. Add the assertion to the test plan (see §5, T-6).

---

## P1

### P1-1 — The design names a mint path E3 does not use

Design §6 assigns "refuse mint if type not in registry (defense in depth)" to
`EntityRegistry.resolve_t0`, and analysis §1 states "Entity identity goes through T0
resolution (`EntityRegistry.resolve_t0`), which **mints** a row in `entities`".

E3 does not call that method. `_normalize_claim` resolves through
`self._resolver.resolve(...)` — `CascadeResolver.resolve` (`workers/e3.py:273, 282, 319`) —
whose miss path is `CascadeResolver._mint` (`spine/resolver.py:419`), with its own
`_INSERT_ENTITY` at `spine/resolver.py:698`. `EntityRegistry.resolve_t0` is a separate
T0-only implementation that the normalize path does not reach.

Consequence: the last-resort net described in §6 and §9 would be installed where it cannot
fire, and the real mint site stays unguarded — while §9 still promises "FK still fires → gate
incomplete", a guarantee nothing implements.

**Fix.** Correct both docs to name `CascadeResolver.resolve` / `CascadeResolver._mint`, and put
the assert immediately before `_INSERT_ENTITY` in `_mint`. If `EntityRegistry.resolve_t0` is
still live for other callers, guard it too and say so — but it is not the E3 path.

### P1-2 — The incident's actual code path is never identified

The relation path **already fails closed on unknown types**. `_signature_allows` opens with:

```python
if subject_type not in type_parents or object_type not in type_parents:
    return False          # workers/e3.py:353-354
```

and it is called with the **LLM-emitted** types at `workers/e3.py:258`, *before* any
`resolve`. `type_parents` comes from `entity_type_parents`, whose SQL is
`SELECT type, parent_type FROM entity_types WHERE deployment_id = :deployment_id`
(`spine/fact_catalog.py:512-517`) — unfiltered, i.e. **exactly** the row set the
`entities_deployment_id_type_fkey` references. So an illegal relation type cannot reach a mint.

The observation loop (`workers/e3.py:318-337`) resolves `observation.subject` with **no type
gate at all**. That is the only path in `_normalize_claim` that can reach `_mint` with an
unregistered type, and therefore the path that dead-lettered BEAM.

The analysis half-notices this in §8 ("illegal types may already be signature-rejected for
some triples — still gate types for **observations**") but hedges it into an implementation
note. The binding design then treats relations and observations symmetrically and never tells
the reader that one of the two was already safe. A cold reader cannot tell what actually broke.

**Fix.** State it in design §2 and §7: *the observation path is the live hole; the relation
path is already fail-closed via the signature gate, and this design's relation-side value is
recall recovery, not FK prevention.* This single sentence also settles P1-3 and re-aims the
test plan (§5).

### P1-3 — Retry trigger scope and an unmeasured cost denominator

§5 retries whenever `types_in(response) - allowed_types` is non-empty — including illegal
types on relations that today are dropped safely and for free by the signature gate, and
including relations whose predicate is unknown and which are discarded regardless.

Two problems follow:

1. **The cost model is anchored to the wrong population.** Analysis §6 assumes ≪1% of claims
   and estimates ~15 extra calls on 15k. That is the rate of claims *that FK'd* — but the
   retry fires on the rate of claims *with any junk type anywhere in the response*, which is
   strictly larger and currently **unmeasured**, because those relations log as
   `"signature-rejected %r (%s -> %s)"` (`workers/e3.py:265-271`) — a line that does not
   distinguish "type not in registry" from a genuine domain/range mismatch. Nobody can
   currently say whether that rate is 0.1% or 8%.
2. **Retries are spent on assertions already discarded** for an unrelated reason.

Note the trade-off honestly: broad triggering is not simply wrong — retrying a claim whose
relation carried a junk type genuinely recovers a relation that is silently lost today. That
is a **deliberate recall expansion**, and it should be presented as one rather than arriving
as a side effect of a gate described as an FK fix.

**Fix.** Pick one and write it down:
- **(a) Narrow** — evaluate the trigger only over types that can reach a mint: observation
  subjects, plus relations that survive the predicate gate. Cheapest, matches the incident.
- **(b) Broad, declared** — keep the current trigger, add a §2 paragraph stating that the
  design also recovers relations currently lost to unknown types, and re-derive §6 of the
  analysis against the true rate.

Either way, **instrument before you tune**: split the existing signature-reject log into
`unknown_type` and `signature_mismatch` so `MAX_INNER_ATTEMPTS` is chosen against a measured
rate rather than an assumed one.

### P1-4 — Why the retry works at all is unstated (Rule 1)

`E3_NORMALIZER_VERSION = "e3-normalize-2026.07b:temp0-1"` pins `temperature=0.0`, and
`_normalize_claim` passes `temperature=0.0` (`workers/e3.py:233`). **At temperature 0 a bare
re-call of the same prompt is a no-op** — nominally the same output for the same input. The
entire recovery value of this design rests on the corrective suffix changing the prompt.

The design gets the mechanism right (§5 adds `retry_suffix(illegal, allowed_types)`), but
never explains this, and leaves the suffix as "normative intent". CLAUDE.md Rule 1 is explicit:
naming a technique is not explaining it; the reasoning must live in the doc.

Two consequences a cold reader needs stated:
- The suffix is **load-bearing**, not advisory. A weak suffix makes the whole retry budget waste.
- If the model's error is **systematic** — it believes "caching process" is a `Process` and no
  allowed type fits — the retry fails deterministically, 100% of the time, at 2× cost and 0%
  recovery. That is what makes the `recovered / unknown_type_events` metric the decisive signal
  for whether `MAX_INNER_ATTEMPTS` should be 2 or 1, and what motivates P1-5.

**Fix.** Add the temperature-0 paragraph. Make the suffix normative: it must (i) list the exact
rejected tokens, (ii) restate the full allowed set, (iii) instruct that every `type` field be
one of those tokens verbatim, and (iv) instruct that if no allowed type fits, the assertion be
omitted rather than forced — an explicit "emit nothing" escape is what converts a systematic
failure into a clean drop instead of a second junk label.

### P1-5 — No job-level retry breaker

Worst case is not the rare fluke the analysis models. If a model upgrade, a prompt regression,
or a deployment missing a commonly-needed pack type pushes the unknown-type rate high, **every
claim retries**: on a 15k-claim version job that is 15k extra LLM calls and roughly 2× wall
clock, with no ceiling, no alarm, and (per P0-2) half of it unbilled. Doubling the runtime of an
already long version-scoped job also pushes against the work-ledger lease.

The design bounds cost **per claim** and not at all **per job**.

**Fix.** Add a per-job breaker to §5: track unknown-type events against claims processed within
the job; once the share exceeds a threshold (a measured starting point, e.g. a few percent, over
a minimum sample of claims so early noise cannot trip it), stop retrying for the remainder of the
job and go straight to drop. Emit `e3.unknown_entity_type_retry_suppressed` with the observed
rate. This stays inside D85 — no coercion, no dead-letter, drops are still drops — while making
the systemic case cheap and loud instead of expensive and quiet.

### P1-6 — Retry-response substitution is unspecified

§5 does `response = generate(normalize prompt + retry_suffix(...))` — the retry response
**replaces** attempt 1 wholesale. If attempt 1 yielded three legal relations plus one illegal,
and the retry returns one legal relation, two good relations are silently lost by the mechanism
meant to *reduce* loss. The design never says whether the responses are replaced or merged.

Merging is not obviously better: `upsert_relation` collapses duplicate
`(subject, predicate, object)` so relations would union cleanly, but observations would deliver
duplicate statements into the D43 adjudicator.

**Fix.** State the rule explicitly. Recommend **last-response-wins** (simplest, order-independent,
no duplicate observation statements) with the loss consequence written down. If loss turns out to
matter, the tighter rule is "accept the retry only if its illegal-type set is strictly smaller,
else keep attempt 1 and drop its illegal-bearing assertions" — but pick one in the doc, do not
leave it to the implementer.

### P1-7 — A failing retry call re-creates the bug class

§5 assumes `generate` returns. It can raise: provider error, timeout, or a
`NormalizationResponse` schema-validation failure on the retry output. Nothing in the design
catches it, so the exception escapes `_normalize_claim` → escapes `handle` → work-ledger
retries and dead-letters the version job. The recovery path would then become a *new* way to
lose the whole document to one bad claim — precisely what §1 forbids.

**Fix.** §5 must state: a failed retry call is treated as "still illegal" — log, count
(`e3.unknown_entity_type_retry_failed`), drop the claim's illegal-bearing assertions, continue
to the next claim. Only genuinely systemic failures (DB down, auth) propagate to the ledger, and
those are already distinguishable at the provider adapter boundary. Add to the test plan (T-7).

### P1-8 — Over-drop on the registry-hit path

The emitted type only matters **at mint**. `CascadeResolver.resolve` returns the *stored* type
on any hit (`spine/resolver.py:118-124`, `entity_type=exact["type"]`; same for T1–T4 candidate
hits). So an observation about an entity that already exists — "caching process" already minted
as a `Concept` by an earlier document — resolves correctly today even when the LLM emits
`Process`, and no FK occurs. This design drops it anyway.

That is a defensible choice, but it is a **silent recall cost on a path that was never broken**,
and the design does not mention it.

**Recommendation: keep the uniform drop, and say why.** Gating on registry existence instead
would make the outcome depend on ingest order — the same claim lands or is dropped depending on
whether some other document minted the lemma first — which is worse for a system whose
observations feed adjudication. Uniform drop is order-independent and matches D18's position
that an out-of-vocabulary type is an invalid assertion regardless of who else has been minted.
Write that paragraph into §7 and note the recall cost in §9.

### P1-9 — `E3_NORMALIZER_VERSION` is unaddressed

That constant is doing three jobs: the normalize stage's ledger `component_version`
(`workers/e1.py:636`), the `normalizer_version` provenance stamped on every relation
(`workers/e3.py:314`), and a record of generation parameters (`":temp0-1"`, with the docstring
"generation parameters are part of provenance"). This design changes the prompt and the gate
semantics — i.e. the same claim can now produce different facts — and never mentions the version.

Leaving it unbumped stamps new-generation facts with the old generation's string.

Two facts that make the bump cheap and that the design should carry, because an implementer will
otherwise fear a full re-normalize:
- The replay marker `normalized_claim_ids` queries `relation_evidence` / `observation_evidence`
  by `claim_id` with **no version filter** (`spine/entity_registry.py:199-205`), so already-
  normalized claims short-circuit regardless of the version bump.
- Only claims that produced *zero* facts get re-normalized — which, note, is the same population
  as the retried-then-dropped claims (see P2-8).

**Fix.** Bump to a new generation string, and state in §12/§9 what the DLQ replay in the recovery
plan means under the new version (replay grants attempts to the *existing* row, which carries the
old `component_version` — that still works; new enqueues create rows under the new one).

### P1-10 — Metric label cardinality

§8's event table puts `illegal_types[]` and `claim_id` in the field list, and §6 says "Counters
or structured warning attributes" without separating the two. `illegal_types` is an
LLM-generated string — effectively unbounded and attacker-influenceable via document content.
As a counter dimension it will blow up any metrics backend; `claim_id` is unbounded by construction.

**Fix.** Split the table explicitly. Counters carry only bounded dimensions:
`deployment_id`, `site` (`relation_subject` | `relation_object` | `observation_subject`),
`attempt`, `outcome` (`recovered` | `dropped` | `suppressed` | `retry_failed`). Type strings and
claim ids go to structured logs, plus the bounded aggregate in P2-8 for the top-N view §8 wants.

### P1-11 — D66 same-PR docs obligation is missing from the plan

CLAUDE.md makes docs a standing obligation, and this change alters operator-visible behavior that
the shipped site currently describes differently:

- `website/src/app/docs/ingestion/pipeline/page.mdx:27` — "Workers use the deployment work
  ledger: retry, then dead-letter — **never silent skip**." After this design, normalize
  *does* silently drop assertions. That line becomes untrue as written.
- `website/src/app/docs/troubleshooting/page.mdx` §3 ("Stage in retry / dead_letter") is the
  playbook operators use for exactly this incident; the new counters and the "job completes with
  drops" outcome belong there.

§12 has no docs step.

**Fix.** Add the docs obligation to the implementation plan (which itself should move — P2-3):
amend the pipeline page's ledger sentence to distinguish *job-level* dead-letter from
*assertion-level* re-derivable drops, and add the unknown-type counters to troubleshooting.

---

## P2

- **P2-1 — Rule 2 framing.** Binding docs carry MVP/phasing language CLAUDE.md rules out:
  "**Constants (v1):**" (design §5), "(future proposal)" (§3), "defer unless other error classes
  demand it" (§11), and "Per-claim work-ledger fan-out (deferred)" in D85. Rule 2 is explicit that
  numbers are "starting points to be measured, not committed constants", and that deferral framing
  belongs in `plan/plans/`. Relabel the constants table as measured starting points; recast
  per-claim fan-out as a documented alternative / non-goal rather than a later phase. (The analysis
  doc's "Deferred: F" is fine — `plan/analysis/` is explicitly allowed to be messy.)
- **P2-2 — Status line.** "accepted for implementation (pending dual design review on PR)" asserts
  both states at once. It is either proposed-pending-review or accepted; pick one.
- **P2-3 — §12 belongs in `plan/plans/`.** Implementation sequencing is not design content, and
  grep confirms this is the only doc in `plan/designs/` carrying an "Implementation checklist".
  Move it; keep §9's recovery expectation (DLQ replay completes without human force-succeed) in
  the design, since that is a behavioral claim.
- **P2-4 — Health bands.** §8 derives `dropped / unknown_type_events` but then states bands as
  "% of claims" — two different denominators for one number. Also there is **no band on the event
  rate itself**: 30% of claims raising unknown types with 100% recovery passes the stated bands
  while being a broken prompt and a doubled bill. Add an event-rate band; it is the input to P1-5's
  breaker threshold.
- **P2-5 — Exact match is stricter than necessary.** §4 mandates case-sensitive exact match, no
  fuzzy match. That means `"person"`, `"Person "`, or `" Person"` each cost a full LLM round-trip
  to fix a token the model already got right. Trimming surrounding whitespace and matching
  case-insensitively **against registry keys** is a normalization onto an existing registered
  token, not the coercion product rejected — no type is rewritten to a *different* type, and D18's
  closed vocabulary is untouched. Recommend allowing it and stating explicitly why it is not
  coercion; otherwise a cold reader will read §4 as forbidding it and pay for retries that resolve
  a capital letter.
- **P2-6 — Gate reordering changes existing log semantics.** §7 puts the type gate ahead of the
  predicate and signature gates. Relations that today emit `"signature-rejected …"` will emit
  unknown-type drops instead. Anything keyed on the old line — dashboards, the BEAM findings
  notes — changes meaning at the same moment the new counters appear. Note it in §7 so the shift
  is not read as a regression. (The chosen order is otherwise correct and worth keeping for a
  reason the doc does not give: the predicate gate has a *write* side effect —
  `ensure_other_predicate` at `workers/e3.py:247` registers `other:` predicates — so running the
  type gate first keeps a discarded attempt-1 response from polluting the predicate registry. Say
  this, and test it: T-9.)
- **P2-7 — Log volume.** One `_logger.warning` per event on a 15k-claim job is a lot of lines if
  the rate is non-trivial. Recommend sampled detail plus one aggregate summary line per version job
  (counts by site and outcome, top illegal labels) — which is also the artifact an operator actually
  reads after a run.
- **P2-8 — No durable record of drops.** Drops are re-derivable in principle (D2: the claim is
  immutable), but nothing records *which* claims were dropped for *which* type, so "re-run the
  claims we lost now that a `Process` pack is installed" is a log grep against retention. Worse, a
  dropped claim writes no evidence row, so `normalized_claim_ids` never short-circuits it and
  **every replay or version bump re-pays its retries**. A bounded counting table keyed
  `(deployment_id, illegal_type)` with a row cap fixes the metrics side cheaply and doubles as the
  input to the D18 question the incident actually raises: *should this deployment have a pack that
  contains this type?* That is the highest-value operator artifact in this whole design and it is
  currently a log line.

---

## 3. Is retry-then-drop correct, versus coerce?

**Yes — and the drop half is the load-bearing half.** The decision is sound; the design does not
yet carry the reasoning that makes it obviously sound to a cold reader.

**Why coercion is worse than §11's one-liner ("silent type rewrite") admits.** In the registry, the
first mint binds the type for a lemma permanently: every later resolution of that lemma returns the
*stored* type (`spine/resolver.py:118-124`), and nothing in the current cascade re-types an existing
entity. So a single first-sight fluke coerced to `Concept` becomes the permanent, authoritative type
for that entity across every future document — and it then propagates into the D18 signature gate,
where it silently changes which predicates are admissible for that entity forever. Coercion is not a
lossy-but-local fallback; it is a durable write of a fact the system was explicitly told it does not
know. The analysis captures this in §4 ("first-try flukes become permanent Concept"); the *design*
must, per Rule 1, since the design is what an implementer reads.

**Why drop is consistent with the architecture.** D2 makes claims the immutable source of truth and
relations a derived, re-derivable projection; `_normalize_claim` already drops unknown predicates and
signature-rejected triples on exactly that logic, with "re-derivable" in the log line. Unknown types
join a well-established class rather than inventing a new one. Nothing is lost that a re-run after an
ontology change cannot recover — provided P2-8's record exists to tell you *what* to re-run.

**One asymmetry the design should acknowledge.** Drop is not equal-cost across the two sites. A
dropped relation is usually re-asserted by other documents and collapses into the same fact when it
lands (D2/D54). A dropped observation is dropped *before* the D43 adjudicator and is the only channel
carrying attribute and stance content for that claim — there is no dedup path that recovers it from a
sibling document asserting the same thing differently. Since the observation path is also the one that
actually broke (P1-2), the residual-drop metric matters most exactly where the design tracks it least
specifically. Split the drop counter by site (P1-10) and say this in §9.

**Is the retry itself worth keeping?** Yes, but conditionally, and the condition should be written
down: it pays only for prompt-level near-misses, because at temperature 0 a systematic
misclassification recovers 0% at 2× cost (P1-4). Ship it with the recovery-rate metric and the
job-level breaker (P1-5), and be prepared to set `MAX_INNER_ATTEMPTS` to 1 if measured recovery is
low. That is not phasing — it is the measured-constant discipline Rule 2 asks for.

**One alternative the design does not consider and should record as rejected:** having the normalizer
emit *no* type for an entity it cannot type, letting the mint fail closed on a null type rather than an
invented one. Rejected because `EntityRef.type` is structurally required and a null-typed entity has no
signature behavior — but a cold reader will think of it, and §11 is the place to say why not.

## 4. Metrics completeness

The four events in §8 cover the happy taxonomy and miss most of what an operator needs at 03:00.

**Missing entirely:**

| Gap | Why it matters |
| --- | --- |
| `claims_processed` counter | §8 derives rates against it but never emits it — the denominator exists only as a log count |
| FK-violation counter | §9 calls a surviving FK "a bug: gate incomplete" but names no signal. This is the alarm that the gate itself broke — it needs a counter and an alert, not a traceback |
| `retry_failed` | The P1-7 path — a retry that errored, as distinct from one that returned junk. Different fix, must be distinguishable |
| `retry_suppressed` | Emitted by the P1-5 breaker; without it, the breaker's activation is invisible |
| Retry token/cost attribution | P0-2's separate tier — "how much is this gate costing us" must be answerable from spend, not inferred from event counts × average |
| `site` dimension on the *event* | §8 has `kind` only on the drop event. Relation-side and observation-side events have different causes and different remedies (P1-2) |
| `deployment_id` on all four | Present only on the first event; every one of these is per-deployment in a multi-deployment host |

**Wrong as specified:** unbounded label cardinality (P1-10); mixed denominators and no event-rate band
(P2-4); "top illegal type strings" listed as a derived rate but obtainable only by log aggregation over
whatever retention exists (P2-8).

**Also worth adding, cheaply:** legal-assertion count before vs after a retry, which is the only way
P1-6's silent-loss risk becomes visible in production rather than in a bug report.

## 5. Test plan gaps

§10's four tests are relation-oriented and do not cover the failure that motivated the design. The
listed regression test ("all-legal path unchanged, no extra generate call") is good and should stay.

Missing, roughly in priority order:

- **T-1 (the incident itself).** A claim producing an **observation** whose subject type is unknown:
  no exception, no `INSERT INTO entities` with that type, observation not delivered to the adjudicator,
  job completes. §10's tests all exercise relations — the path that was already fail-closed.
- **T-2 (the D85 invariant).** Handler-level, not claim-level: a version with N claims where claim *k*
  carries an illegal type ⇒ the other N−1 claims still produce facts **and** `HandlerOutcome.follow_up`
  still enqueues both terminal branches (`ADJUDICATE_SUPERSESSION` + `EMBED_CLAIM`). "Must not
  dead-letter the document for one illegal type" is the whole point and no listed test asserts it.
- **T-3 (relations were already safe).** Assert that a relation with an unknown type never reaches
  `resolve` — pinning `_signature_allows`'s fail-closed behavior so a future refactor cannot remove the
  belt while this design's braces are assumed to exist.
- **T-4 (safety net at the right site).** The `CascadeResolver._mint` assert (P1-1) refuses an
  unregistered type and raises the typed soft error — with a companion test that
  `_normalize_claim` never triggers it in normal operation.
- **T-5 (retry prompt content).** The second call's prompt actually contains the rejected token(s), the
  full allowed list, and the omit-if-nothing-fits instruction. A retry with a malformed suffix is a
  silent 2× bill (P1-4) and no other test would catch it.
- **T-6 (billing).** Two `record_call`s with distinct keys for a retried claim; assert the ledger has
  two rows, not one (P0-2). Without this test the regression is invisible forever.
- **T-7 (retry raises).** The retry `generate` raising ⇒ drop + continue, `HandlerOutcome` intact, no
  exception escaping `handle` (P1-7).
- **T-8 (budget boundary).** Exactly two `generate` calls for a persistently-illegal claim — never
  three. Plus the P1-5 breaker: past the threshold, exactly one call per claim and a suppression event.
- **T-9 (no registry pollution from a discarded attempt).** Attempt 1 contains an `other:` predicate and
  an illegal type; assert `ensure_other_predicate` did **not** register the discarded response's
  predicate (P2-6).
- **T-10 (metrics are asserted, not assumed).** At least one test per event type asserting emission and
  field content. "Track failure rates" is half of D85; untested metrics rot silently and nobody notices
  until the incident they were built for.
- **T-11 (matching edge cases).** `"process"`, `" Process"`, `"Process "`, `""` — behavior must be
  pinned whichever way P2-5 is decided.
- **T-12 (replay/idempotency).** Re-running the version job after an all-dropped claim: no crash, no
  double-write, and — worth asserting explicitly — the retry is paid again (documenting P2-8's cost
  rather than discovering it in a bill).

## 6. Implementation risks

**Retired by inspection** (worth recording so the implementer does not re-litigate them):
`entity_type_parents` returns exactly the FK's referenced row set — unfiltered, no status column — so
`allowed_types = set(type_parents)` is provably the right allow-list, and the prompt's type list and the
gate's allow-list cannot drift apart.

**Live risks:**

1. **Silent cost amplification** (P1-5) — the top risk. Unbounded per job, invisible in spend (P0-2),
   and it degrades wall-clock on an already long version-scoped job. Mitigation: breaker + separate
   retry billing tier + an event-rate band.
2. **Lease interaction.** Doubling the runtime of a 15k-claim version job pushes against the work-ledger
   lease; expiry mid-job means duplicate execution. The claim-level replay marker makes duplicates
   mostly idempotent, but retried-then-dropped claims — which leave no marker — are re-normalized and
   re-billed by the duplicate runner. Verify the lease/heartbeat headroom against the *retried* worst
   case, not the current one.
3. **Gate placement drift.** §7's ordering is load-bearing in two non-obvious ways: illegal types must
   be filtered before `resolve` (else the cascade spends T3 embeddings and T4 LLM calls on an assertion
   that will be discarded — real money on the failure path), and before `ensure_other_predicate` (else a
   discarded response writes registry rows). Both deserve a comment at the site and a test (T-9), or a
   later refactor will quietly undo them.
4. **Whole-response substitution loss** (P1-6) — a recall regression that would show up as "fewer facts
   after the fix" with no log line explaining it.
5. **Exception on the recovery path** (P1-7) — the most embarrassing failure mode available here: the
   gate built to stop one bad claim from killing a document becomes a second way for one bad claim to
   kill a document.
6. **Snapshot staleness.** `type_parents` is read once per job. A pack installed mid-job means a legal
   type is treated as illegal for the rest of that job (benign over-drop, self-healing on the next run).
   The reverse — a type row deleted mid-job — would defeat the gate and hit the FK; that is exactly what
   the P1-1 safety net is for, and worth one sentence in §9.
7. **Metrics backend cardinality** (P1-10) — a monitoring outage caused by an LLM emitting varied junk
   strings is a self-inflicted second incident.
8. **Version handling** (P1-9) — get the bump decision wrong and either provenance lies or the §12.4
   BEAM recovery does not do what the plan says.

---

## Required before implementation

1. Renumber to **D86**, append at the end of `decisions.md`, fix cross-references (P0-1).
2. Distinct `call_key` (and retry tier) for the retry call, in the design text (P0-2).
3. Correct the mint path to `CascadeResolver` in both docs; relocate the safety net (P1-1).
4. State that the observation path is the live hole and the relation path is already fail-closed;
   choose and document the retry trigger scope (P1-2, P1-3).
5. Add the temperature-0 rationale and make the retry suffix normative, including the
   emit-nothing escape (P1-4).
6. Add the job-level retry breaker (P1-5).
7. Specify response substitution and retry-failure handling (P1-6, P1-7).
8. Decide `E3_NORMALIZER_VERSION`, and state what §12.4's replay means under it (P1-9).
9. Rewrite §8: bounded counter dimensions, add the missing events and the denominator, fix the
   bands (P1-10, P2-4).
10. Add the D66 docs obligation, including the now-untrue "never silent skip" line (P1-11).
11. Expand §10 with T-1, T-2, T-6, T-7, T-10 at minimum (§5 above).
12. Strip Rule 2 framing; move §12 to `plan/plans/`; fix the status line (P2-1, P2-2, P2-3).

## What the design gets right

Worth stating plainly, because the change list above is long and the core is sound: the policy choice
is correct and well-argued; gating **before** `resolve` (§7) is the right placement both for correctness
and for cost; refusing to auto-register types holds the D18 line under exactly the pressure that erodes
closed ontologies in practice; the explicit refusal to use the FK `IntegrityError` as a control path
(§6) is the right instinct; and framing the failure as a control-plane gap rather than a migration gap
(analysis §9) is the correct diagnosis. The gap is not the decision — it is that the document does not
yet tell a cold reader which code actually broke, where the mint really happens, or what the retry costs
when it does not work.
