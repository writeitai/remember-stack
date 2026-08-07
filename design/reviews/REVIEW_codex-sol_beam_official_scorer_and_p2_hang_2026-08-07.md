# Adversarial review: BEAM official scorer and P2 projection hang

Review date: 2026-08-07

Reviewer identity: `codex-sol`

Reviewed commit: `2a94ffd2bace6ddfb2de368306202ef0409b9d7f` (`#226`)

## Verdict

**Request changes.**

The scorer is a substantial improvement over substring containment. The committed
100K/1 rubric fixture is semantically identical to upstream BEAM's fixture, the
judge prompt is an exact copy after trimming its Python string delimiters, and the
core nugget mean plus LLM-aligned Kendall/F1 helper follows the shape of upstream
`compute_metrics.py`. The six offline tests, Ruff, and Pyright all pass.

It should not yet be treated as a trustworthy “official” score, however. A correct
event-ordering answer written in the same one-line numbered style as BEAM's own
committed ideal answer cannot obtain a perfect ordering score. The report also
mixes paper-corrected behavior, an alternative judge model, and a local choice of
event-ordering primary metric without recording a compatibility profile. On the
operations side, a normal smoke run makes at least 54 paid judge calls and the
event aligner permits an unbounded number; there is no retry, checkpoint, global
deadline, or cost ceiling. The P2 incident note correctly identifies the blocking
query, but its cleanup commands can affect unrelated sessions and snapshots.

No P0 is present, so no code fix was made as part of this review.

## P0 findings

None.

## P1 findings

### P1.1 — Event ordering is accidentally a line-format metric

`score_item_official` defines the predicted event list as the non-empty lines of
the response (`benchmarks/rs_harness_beam/official_score.py:357-363`). Both
committed BEAM ideal answers are instead single-line prose with inline numbered
events (`benchmarks/rs_harness_beam/fixtures/beam_smoke_100k_1/probing_questions.json:80-110`).
Consequently, a response that repeats the first ideal answer exactly is presented
to `event_ordering_score` as one compound event, not three events.

This is not cosmetic. In a deterministic proof using an equivalence judge that
accepts a rubric event whenever it appears in the predicted text, the exact
one-line three-event answer scores:

```text
precision=1.0, recall=0.3333, f1=0.5,
tau_norm=0.908248, final_score=0.454124
```

The same three events on separate lines score `1.0`. A stricter “same event/fact”
judge can reject the compound line entirely and produce `0.0`. Thus answer
formatting, rather than event presence and order, can dominate the primary score.
Upstream has the same raw `split("\n")` behavior
([`compute_metrics.py:396-410`](https://github.com/mohammadtavakoli78/BEAM/blob/3e12035532eb85768f1a7cd779832b650c4b2ef9/src/evaluation/compute_metrics.py#L396-L410)),
but inheriting an upstream defect does not make the local result valid—especially
when the CLI accepts arbitrary harness answers and calls itself the paper scorer.

Before relying on the result, parse common numbered/bulleted list forms
deterministically, or impose and validate a one-event-per-line answer contract at
answer-generation time. Add a regression asserting that each committed ideal
event-ordering answer receives `1.0` under deterministic equivalence.

### P1.2 — “Official” currently conflates incompatible BEAM variants

The report calls itself `beam_official_nugget_llm_judge`
(`benchmarks/rs_harness_beam/official_score.py:459-470`) and the CLI advertises a
“BEAM paper scorer” (`benchmarks/rs_harness_beam/cli.py:136-150`), but the output
does not reproduce one clearly named upstream protocol:

- RememberStack defaults to `openai/gpt-5.6-luna` through OpenRouter
  (`benchmarks/rs_harness_beam/official_score.py:44-45`,
  `benchmarks/rs_harness_beam/cli.py:147-150`). The pinned upstream evaluation
  constructs `gpt-4.1-mini` directly
  ([`src/llm.py:61-68`](https://github.com/mohammadtavakoli78/BEAM/blob/3e12035532eb85768f1a7cd779832b650c4b2ef9/src/llm.py#L61-L68)).
- RememberStack correctly preserves `0.5` for every ability. Upstream converts
  judge scores with `int(...)` for the nine non-event abilities, truncating `0.5`
  to `0`, while event ordering uses `float(...)`
  ([`compute_metrics.py:339-365`](https://github.com/mohammadtavakoli78/BEAM/blob/3e12035532eb85768f1a7cd779832b650c4b2ef9/src/evaluation/compute_metrics.py#L339-L365),
  [`compute_metrics.py:412-431`](https://github.com/mohammadtavakoli78/BEAM/blob/3e12035532eb85768f1a7cd779832b650c4b2ef9/src/evaluation/compute_metrics.py#L412-L431)).
  The local choice matches the paper's 0/0.5/1 definition, but not repository
  output produced by that code.
- RememberStack replaces `<question>` in the unified prompt
  (`benchmarks/rs_harness_beam/official_score.py:221-228`). Upstream's prompt
  contains the placeholder, but every evaluator replaces only `<rubric_item>` and
  `<llm_response>`, leaving `<question>` literal. The local correction is sensible
  and more faithful to the prompt's intent, but it changes judge outcomes.
- `compute_metrics.py` calculates both normalized tau and `tau_norm * f1`
  ([`compute_metrics.py:270-308`](https://github.com/mohammadtavakoli78/BEAM/blob/3e12035532eb85768f1a7cd779832b650c4b2ef9/src/evaluation/compute_metrics.py#L270-L308)).
  RememberStack chooses the product as event ordering's `primary_score`
  (`benchmarks/rs_harness_beam/official_score.py:418-421`). The upstream reporting
  path instead averages `tau_norm` for event ordering
  ([`report_results.py:37-47`](https://github.com/mohammadtavakoli78/BEAM/blob/3e12035532eb85768f1a7cd779832b650c4b2ef9/src/evaluation/report_results.py#L37-L47)),
  while paper §2.4 describes Kendall tau-b as the event-ordering exception and
  does not state that the displayed ability metric is the F1 product
  ([paper §2.4](https://arxiv.org/html/2510.27246v2#S2.SS4)).
- `overall_mean` is a RememberStack item-weighted composite
  (`benchmarks/rs_harness_beam/official_score.py:445-470`). Upstream
  `run_evaluation.py` stores per-item results by ability and does not define that
  field
  ([`run_evaluation.py:22-87`](https://github.com/mohammadtavakoli78/BEAM/blob/3e12035532eb85768f1a7cd779832b650c4b2ef9/src/evaluation/run_evaluation.py#L22-L87)).

Therefore the reported `~0.5625` is a useful RememberStack smoke result, but it is
not directly comparable to BEAM paper tables or a run of the pinned repository.
Define explicit profiles such as `beam-paper-corrected` and
`beam-repo-3e120355`, name the current result as a RememberStack adaptation, and
record the selected profile, upstream SHA, judge provider/model, prompt hash,
rubric hash, and event-ordering primary metric in the report.

### P1.3 — Paid judge work is unbounded and non-resumable

The 100K/1 fixture contains 54 rubric nuggets, so even before event alignment a
complete smoke score makes 54 serial API calls. For normal three-line and
five-line event answers, greedy alignment can add up to 34 calls, for 88 calls
total. At the hard-coded 120-second per-call timeout
(`benchmarks/rs_harness_beam/official_score.py:159-165`), that permits roughly
176 minutes before a normal-shape run fails.

More importantly, event alignment is `O(reference events × response lines)`
(`benchmarks/rs_harness_beam/official_score.py:254-273`) and response lines are
uncapped (`benchmarks/rs_harness_beam/official_score.py:357-360`). A system under
evaluation can emit thousands of short lines and indirectly trigger thousands of
paid judge requests. Answer and prompt byte lengths are also uncapped.

There is no retry/backoff for 429/5xx/transient network errors, no global deadline,
no call or dollar budget, no usage capture, and no per-nugget checkpoint. The
report is written only after every item completes
(`benchmarks/rs_harness_beam/official_score.py:400-475`). One failure after dozens
of successful calls loses all progress, and rerunning pays for those calls again.
`urllib.error.URLError`, socket timeout, and response JSON decoding are not wrapped
as `OfficialScoreError`, so the CLI's only exception handler does not contain
common failures (`benchmarks/rs_harness_beam/official_score.py:198-209`,
`benchmarks/rs_harness_beam/cli.py:90-100`).

Cap event candidates and input bytes before making calls; provide a preflight call
and maximum-cost estimate; add a configurable per-call timeout plus global
deadline; retry only safe transient failures with bounded backoff; checkpoint each
judge result under a prompt/model/rubric cache key; and support idempotent resume.

### P1.4 — The documented P2 cleanup commands are over-broad and can damage healthy work

The cancellation query matches every active query whose text contains
`graph_edges_visible_history` (`design/operations/p2-projection-hang-beam-smoke.md:50-54`).
It does not exclude `pg_backend_pid()`, restrict by application/user/deployment,
or identify PIDs first. The cleanup statement itself contains that string and can
match its own active backend; it can also terminate unrelated analytical queries
or a healthy projection. `pg_terminate_backend` aborts the whole session rather
than first attempting the less disruptive `pg_cancel_backend`.

The snapshot workaround is broader still: its only shown predicate is
`status='building'` (`design/operations/p2-projection-hang-beam-smoke.md:54-55`).
As written, an operator could mark every P2 and P3 build in every deployment
failed, including builds that are still live. The `validation=...` fragment is
also not executable SQL, which encourages improvised production updates.

Replace these snippets with a two-stage procedure: inspect and record exact PIDs,
application identity, query age, and snapshot IDs; cancel only selected non-self
backends; terminate only if cancellation fails; then update exact stale snapshot
IDs after verifying no worker owns them. Require deployment, plane, age, and
snapshot predicates. State the privilege and transaction-loss implications.

### P1.5 — The copied BEAM data has no local license/provenance notice

The committed `probing_questions.json` is semantically identical to upstream
`chats/100K/1/probing_questions/probing_questions.json`, and the prompt text is
also exact. Upstream licenses code under MIT and BEAM benchmark data under CC
BY-SA 4.0
([upstream README:257-267](https://github.com/mohammadtavakoli78/BEAM/blob/3e12035532eb85768f1a7cd779832b650c4b2ef9/README.md#L257-L267)).
RememberStack's root distribution declares only Apache-2.0, and there is no
third-party notice or adjacent license/provenance file for the fixture. A generic
source URL in `official_score.py:18-20` is not a clear license declaration for the
redistributed data.

Before distributing the fixture, add the required attribution, upstream commit
and source path, applicable CC BY-SA notice/link, and modification statement (the
JSON was reformatted). Confirm with the project's license owner whether the prompt
should be recorded under upstream's MIT code license and keep the two provenances
distinct.

## P2 findings

### P2.1 — Off-scale judge values silently become benchmark scores

The paper and prompt permit exactly `0`, `0.5`, and `1`, but `_clamp_score`
accepts any numeric value and merely clamps it to `[0, 1]`
(`benchmarks/rs_harness_beam/official_score.py:144-153`). A malformed judge output
of `0.7` therefore becomes an undocumented fourth score. Reject off-scale values
or map them according to an explicitly versioned policy; do not silently broaden
the metric.

### P2.2 — Duplicate events and loose YES parsing can inflate alignment

Alignment is one-to-one only while canonicalizing. A repeated system item that is
textually equal to a reference item can remain equal after its first match is
consumed. The subsequent set intersection and membership-based FP calculation
collapse or ignore that duplicate (`benchmarks/rs_harness_beam/official_score.py:310-320`),
and rank dictionaries collapse duplicates again (`benchmarks/rs_harness_beam/official_score.py:321-328`).
This can fail to penalize violation of “ONLY N items.” Preserve occurrence
identity through precision/recall and ranking, or reject duplicate predictions.

`llm_equivalence` treats any response containing the substring `yes` as a match
(`benchmarks/rs_harness_beam/official_score.py:242-251`). `not yes`, a repeated
`YES/NO` instruction, or another stray token can become a false positive. Parse an
anchored enum and retry/reject everything else.

### P2.3 — Tests cover helpers, not the scoring protocol

The six tests exercise JSON parsing, an arithmetic mean, one fixture lookup, a
missing lookup, identical-list Kendall tau, and basic fixture parsing
(`src/tests/benchmarks/test_beam_official_score.py:18-75`). They never call
`judge_nugget`, `align_events_with_llm`, `event_ordering_score`,
`score_item_official`, `score_run_dir_official`, or either CLI path. They would
not detect P1.1, an event primary-metric change, API payload drift, missing-answer
behavior, partial-output loss, or an off-scale score.

Add deterministic fake-judge tests for perfect/reversed/missing/extra/duplicate
and inline-numbered event answers; 0/0.5/1 enforcement; question substitution;
provider failures; all 20 questions; aggregation and report metadata; CLI exit
codes; unique/missing IDs; and resume behavior. Compare the local tau helper to
SciPy on a matrix of relevant missing-item/tie cases, while defining singleton
and empty-list behavior explicitly.

### P2.4 — Input and network errors escape the CLI's documented failure path

`load_beam_rubrics` validates only the root object and then returns a much more
specific type than it proved (`benchmarks/rs_harness_beam/official_score.py:90-98`).
Question objects, ability names, unique/non-empty IDs, duplicate question text,
answer payloads, and rubric strings are not preflighted. A missing ID becomes the
literal ID `"None"`; an absent answer becomes an empty response and still incurs
paid judging (`benchmarks/rs_harness_beam/official_score.py:400-409`). File-not-found,
JSON decode, permission, `URLError`, and timeout failures bypass the CLI's
`OfficialScoreError` handler and can expose a traceback.

Validate the complete run and rubric schema before the first API call, distinguish
missing answers from intentional empty answers, reject duplicate IDs, and map
expected file/network/provider failures to concise actionable errors.

### P2.5 — Reproducibility and confidentiality metadata are incomplete

Temperature zero does not make a routed LLM judge deterministic. The report lacks
the prompt/rubric content hashes, upstream SHA, response-format policy, OpenRouter
route/provider, request count, token usage, cost, latency, retry history, and tool
version (`benchmarks/rs_harness_beam/official_score.py:459-471`). Without them, a
later rerun cannot explain score drift.

Questions, rubrics, and complete answers are sent to OpenRouter and complete
answers plus judge reasons are written into the report
(`benchmarks/rs_harness_beam/official_score.py:187-199`,
`benchmarks/rs_harness_beam/official_score.py:423-441`). This is acceptable for
the synthetic committed smoke fixture, but `--rubrics` and `--run` allow arbitrary
potentially sensitive data with no disclosure or redaction mode. Add an explicit
third-party-data warning and a report mode that omits or hashes raw responses.

The CLI also supports `--api-key` (`benchmarks/rs_harness_beam/cli.py:152-155`),
which can leak the secret through shell history and process inspection. Prefer the
environment/secret-file route and deprecate the command-line value. Positively,
the key is not placed in the report or normal success output, and the fixed HTTPS
endpoint uses Python's normal certificate validation.

The raw response is embedded in an LLM judge prompt and is therefore also a prompt
injection surface. Treat judge scores as untrusted benchmark measurements, use a
strict structured-output schema where supported, add injection adversarial tests,
and document that no LLM-as-judge result is a security boundary.

### P2.6 — The P2 diagnosis is sufficient for incident triage, not root-cause closure

The incident note gives useful evidence: the exact active query, observed ages,
timeouts against `entities_current`, small base-table counts, and the absence of
Parquet output (`design/operations/p2-projection-hang-beam-smoke.md:3-48`). That is
enough to stop operators waiting for offline graph construction and to localize
the stall to PostgreSQL before export.

It is not sufficient to close the root cause or choose a safe SQL rewrite:

- The claimed `EXPLAIN` is summarized only as cost `~10^7`; no plan artifact,
  `EXPLAIN (VERBOSE, SETTINGS, FORMAT JSON)`, PostgreSQL version, migration SHA,
  database statistics state, or relevant settings are preserved
  (`design/operations/p2-projection-hang-beam-smoke.md:32-35`).
- The note attributes two `entities_current` expansions to the outer
  `graph_edges_visible_history` semijoins. Those do exist
  (`src/rememberstack/spine/migrations/versions/p9_05_0026_graph_helpers.py:84-114`),
  but `facts_visible_history` already reads `v_memory_fact_visible`, whose relation
  arm joins `entities_current` for both survivor endpoints
  (`src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py:487-540`,
  `src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py:685-739`).
  The expanded watermark plan can therefore contain four endpoint-membership
  expansions, before considering evidence/count subviews. The note misses this
  likely redundancy.
- The graph export transaction disables JIT and constrains join planning but sets
  no statement or lock timeout
  (`src/rememberstack/spine/projection.py:143-160`). A dead or very slow statement
  can hold a repeatable-read transaction and a `building` registry row indefinitely.
- No before/after acceptance target is defined. “Does not hang” should become a
  bounded smoke assertion with query duration, row equality, watermark equality,
  and zero stale `building` snapshots.

Check in the anonymized JSON plan and environmental metadata, capture a bounded
`EXPLAIN ANALYZE` only after a safe statement timeout is installed, and verify the
four-expansion hypothesis directly.

## Faithfulness to upstream BEAM

The reference used for this review is upstream commit
[`3e12035532eb85768f1a7cd779832b650c4b2ef9`](https://github.com/mohammadtavakoli78/BEAM/commit/3e12035532eb85768f1a7cd779832b650c4b2ef9),
the current upstream `main` at review time.

| Aspect | Assessment |
| --- | --- |
| 100K/1 rubrics | Faithful. Parsing both JSON files yields equal objects for all ten abilities, two questions per ability, and 54 total nuggets. Local whitespace/indentation differs. |
| Unified judge prompt | Faithful fixture. The local text equals upstream `unified_llm_judge_base_prompt` after removing Python delimiters and outer whitespace. |
| Rubric association | Deliberate divergence. Upstream associates by ability and list index (`run_evaluation.py:12-19`, `:34-43`); local uses ability plus exact stripped question text (`official_score.py:101-122`). This is safer against reordered harness questions but should reject duplicate question text. |
| Nugget prompt | Paper-corrected divergence. Local replaces `<question>`; upstream accidentally leaves it literal. |
| Nugget scale | Paper-corrected divergence. Local preserves `0.5`; upstream truncates it for nine abilities. This is a reason to call the mode “paper-corrected,” not evidence of byte-for-byte repository compatibility. |
| Judge | Material divergence. Luna/OpenRouter replaces upstream GPT-4.1-mini/OpenAI. Results need a distinct protocol identity. |
| Event alignment | Mostly faithful to `compute_metrics.py`: greedy one-to-one LLM equivalence, set-style F1, union ranks, normalized tau-b, and product are carried over. Local removes blank lines and strips each item, whereas upstream passes raw split lines. Both retain the line-format and duplicate limitations. |
| Kendall implementation | Reasonable dependency-free port for the union-rank shapes used here, but edge cases are untested. Local maps fewer than two rank entries to tau `0` and thus normalized tau `0.5`; SciPy/upstream can produce `NaN` for degenerate inputs. |
| Event primary metric | Ambiguous divergence. Local chooses `final_score`; upstream's helper computes it, upstream's reporter displays `tau_norm`, and the paper text names Kendall tau-b. The choice must be versioned and disclosed. |
| Aggregation | Local ability means match the per-question-then-ability pattern, but `overall_mean` and its mix of nugget versus event product are local additions. |
| JSON recovery | Local accepts bare/fenced objects and a greedy embedded object; upstream additionally invokes `json_repair`. Failure behavior and accepted malformed outputs differ. |
| Execution | Local is serial and single-run; upstream batches directories with workers. This does not change the formula, but it materially changes time, rate-limit, and failure behavior. |

The strongest accurate description today is “BEAM paper-corrected,
RememberStack-adapted scorer,” not an unqualified reproduction of official
published BEAM results.

## Security and operational assessment

### API key handling

- Good: the key is required explicitly, sent only in the authorization header to
  a fixed HTTPS URL, and omitted from reports and success logs.
- Change: remove/deprecate `--api-key`; shell arguments are observable. Prefer the
  existing environment variable or a permission-checked secret-file/stdin option.
- Change: scrub provider exception bodies before printing them and ensure no proxy
  or HTTP debug logging can emit authorization headers.

### Timeouts and recovery

- A 120-second socket timeout is present, but it is not configurable from the CLI
  and there is no whole-run deadline.
- Timeouts, DNS/TLS failures, 429, and 5xx responses need typed handling, bounded
  retries, and retry-after support.
- Persist completed judge decisions atomically and resume by content-addressed key.
  Write final reports atomically so interruption cannot leave a truncated report.

### Cost and resource bounds

- Preflight the fixed 54 nugget calls and the bounded event-alignment maximum.
- Limit response bytes, response lines/events, rubric count, and per-prompt tokens.
- Provide `--max-calls` and `--max-cost`, record actual usage/cost, and abort before
  exceeding either budget.
- Consider bounded concurrency only after rate-limit and checkpoint semantics are
  correct; concurrency alone would reduce elapsed time while increasing burst risk.

### Data and metric integrity

- Warn that arbitrary run/rubric content leaves the machine for OpenRouter.
- Support redacted reports and restrictive file permissions for reports containing
  raw answers and judge reasoning.
- Treat evaluated responses as prompt-injection input. Structured output and strict
  parsing reduce accidental corruption but do not turn an LLM judge into a trusted
  security control.

## P2 hang diagnosis and prioritized fix

The diagnosis is **sufficient as a first incident note**: it tells an operator why
the process is quiet, identifies the precise database statement, and makes clear
that P1-only benchmark work may continue. It is **not sufficient as an engineering
root-cause record** until the plan and environment are captured and the duplicate
endpoint gates are accounted for.

The fix order I would prioritize is:

1. **Bound failure first.** Add configurable transaction-local `statement_timeout`
   and `lock_timeout` before every graph-export statement, phase-start/finish logs,
   and a stale-build reconciler/heartbeat. Ensure timeout exceptions mark the exact
   snapshot failed. This converts an indefinite silent outage into a bounded,
   attributable failure.
2. **Remove redundant work with equivalence proof.** `facts_visible_history`
   already guarantees visible survivor endpoints through `v_memory_fact_visible`.
   Prove with `EXCEPT` in both directions that the outer two endpoint `EXISTS`
   clauses add no membership constraint, then remove them from both current and
   historical graph-edge views. Capture the before/after JSON plans and timings.
3. **Materialize export membership once.** Within the existing repeatable-read
   export, build and index an export-local visible-entity/provenance set once and
   reuse it for Entity, RELATES, MENTIONED_IN, IS_DOCUMENT, and validation. The
   current `graph_survivor` temp table is only the redirect/corruption gate; it does
   not by itself encode the full `entities_current` provenance rule.
4. **Derive the watermark from emitted relations.** Track `max(ingested_at)` while
   materializing or streaming the exact RELATES rows, rather than re-expanding the
   historical view in a separate aggregate. Do not substitute an unrestricted base
   table max: it could advertise a hidden/forgotten relation that the snapshot does
   not contain, violating `GraphExport.watermark()`'s documented bound
   (`src/rememberstack/spine/projection.py:75-83`).

The acceptance test should run on a copy of the BEAM smoke database and assert a
bounded duration, identical visible entity/relation sets, identical watermark,
published snapshot counts matching Parquet/graph counts, no backend older than the
timeout, and no stale `building` rows after forced timeout and worker termination.

## Suggested follow-ups

Required before treating scores as release/benchmark evidence:

1. Fix or validate event-list extraction and add the committed-ideal-answer
   perfect-score regression.
2. Define and record an explicit scoring compatibility profile. Rename the current
   scorer/output if it is not intended to reproduce the pinned upstream reporter.
3. Bound API calls, input size, elapsed time, and cost; add checkpoint/resume and
   typed transient-failure handling.
4. Replace the destructive P2 cleanup snippets with PID- and snapshot-specific,
   inspection-first procedures.
5. Add third-party license/provenance notices for the BEAM prompt and dataset.

High-value next work:

- Expand protocol tests through the CLI and report, using a deterministic fake
  judge; keep live-judge smoke tests opt-in and budget-capped.
- Record prompt/rubric/upstream hashes, provider route, usage, cost, and timing in
  every report; preserve the `~0.5625` run's metadata if it still exists.
- Check in the P2 `EXPLAIN ... FORMAT JSON` artifact and exact environment metadata.
- Ship timeout/progress/stale-build safeguards before the SQL optimization, then
  optimize the redundant endpoint/provenance expansion with equivalence tests.
- Add a small operations test demonstrating that cancel/update examples cannot
  select the current backend, another deployment, another plane, or a fresh build.

## Verification performed

The review read every scoped implementation, CLI, test, prompt, and incident file,
inspected the relevant P2 worker/view definitions, and compared against the pinned
upstream source. No live judge calls were made.

```text
uv run pytest -q src/tests/benchmarks/test_beam_official_score.py
6 passed in 0.03s

uv run ruff check benchmarks/rs_harness_beam/official_score.py \
  benchmarks/rs_harness_beam/cli.py \
  src/tests/benchmarks/test_beam_official_score.py
All checks passed!

uv run pyright benchmarks/rs_harness_beam/official_score.py \
  benchmarks/rs_harness_beam/cli.py
0 errors, 0 warnings, 0 informations
```
