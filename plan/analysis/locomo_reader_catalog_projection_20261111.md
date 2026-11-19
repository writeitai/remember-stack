# Reader tool-catalog projection and answer attention

Non-binding analysis, 2026-11-11. The owner authorized small, general reader
changes, tested on two development questions before a fresh complete conv42
regression. Every candidate must be published before paid testing. The source
store, judge wording and ingestion/reuse generations stay fixed.

The first four prompt-only candidates scored0/2 with Pro HIGH reader and judge.
Their raw source traces include the missing restriction and image subject, but
the final output drops details, adds an unsupported category contrast, or mixes
work media. Trial05 was still running when this catalog candidate was prepared; later
results are recorded below.
All prior outcomes and six-archive GCS readbacks are in cloud PR959,
`experiments/locomo-targeted-development-20261111/`. This is development tuning,
not held-out validation or proof that the engine lacks the source evidence.

The ordinary full reader renders all22 ToolDescriptor records on every step.
Each record includes its complete result JSON Schema, often repeating the same
large response-envelope definition. An empty trace rendered165157 characters;
the two saved final traces rendered375700 and392997 characters. The existing
MCP tool shape omits output schemas and reduces these to25476,236019 and253316
characters. These are measured character counts, not tokenizer measurements or
an attribution of the observed answer failures.

Chosen candidate: omit only `result_schema` from the ordinary reader's displayed
catalog. Keep name, description, full input schema, result contract, grain,
answer intent and other descriptor metadata. The durable tool catalog and hash,
public API/MCP schema, runtime validation and every retrieved response stay
unchanged. This avoids a separate tool catalog, hand-written response summaries,
lossy evidence selection, new ranking model or additional model call. The
reader still sees actual typed response fields in its trace and the existing
instruction to respect freshness, negatives, truncation and attribution.

The alternative of retaining the full schemas preserves maximum advance type
information but repeats a large description the reader does not need to call a
tool. Switching wholesale to the MCP shape also discards useful contract/grain
metadata. Filtering retrieved evidence could save more input but would change
what the reader is allowed to consider and introduce a ranking dependency; it
is outside this small experiment. Re-ingestion is expressly excluded.

Risk: omitting an output schema may make a field unfamiliar before its first
use. Tool descriptions, result contracts and actual trace fields remain the
source of those semantics. This is a benchmark reader projection, not removal
of public schemas or engine validation. Offline checks must prove that all tool
inputs and contracts survive, that output schemas leave only the rendered
catalog, and that retrieved evidence survives unchanged. Scores, latency and
actual usage must be measured separately; a smaller prompt alone proves none
of them improved.

The changed rendering is recorded by the single current Full-v45 adapter
identity; Full-v44 is retired from executable configuration, while recorded
artifacts remain readable. No ingestion key, provider/model default, retrieval
pool, permission, judge prompt, or tool budget changes. Explicit prompt-override
experiments also record the immutable runner revision and template hash. Keep
all failed trials and use a fresh complete20-question arm only after both
targeted questions pass. Do not merge this candidate on a size reduction alone.

Offline projection check for candidate23ab8c81c: the ordinary empty-trace prompt
is29324 characters, and the same two final traces are239867 and257164
characters. This removes135833 repeated catalog characters while retaining
more descriptor metadata than the MCP shape. All394 benchmark tests, Ruff,
docs truth/generated-doc checks and targeted Pyright pass. These checks do not
establish an accuracy gain; paid comparison is still pending.

## Source-qualified answer template in the same draft

Prompt-only trial05 (SHA256
`065f61b33211537404a5fbc6bb0da3d25f0d8ba2bc397816f5d35ebc53e274d0`)
scored1/2 with Pro HIGH for both roles. Its concrete restriction answer includes
the related item under its precise source label and is graded CORRECT. The book
answer includes the pictured subject and a competing recommendation but remains
WRONG. This does not establish that the second answer lacks evidence or that
the judge is necessarily incorrect. Cloud PR959 retains the unmodified result.

The general template interprets short personal-memory questions by everyday
intent, keeping neighboring source labels parenthetically instead of upgrading
them or adding unsupported negative classifications. It checks object kind and
medium before transferring a preference, treats an unnamed object's owning
image as part of its description, and searches the object class without a
resolved-person filter when that filter can hide attachment-derived evidence.
Non-exclusive recommendations are not unique favorites; supported alternatives
may be qualified, while casual mentions are excluded. Compact output must retain
the requested concrete values, not explanations of search sufficiency.

These replace overlapping earlier wording; they contain no named answer,
person, condition or source label. The alternative of injecting a missing answer
or suppressing a retrieved competing candidate would tune to the reference
rather than improve general recall and is rejected. A broader answer can still
include irrelevant material or lead with uncertainty; the complete regression
must expose that. There is no postprocessing, manual grade override or new
retrieval/extraction model. Trial06 holds this template fixed and changes only
the catalog projection. At that snapshot its outcome was pending; the combined candidate remains a
draft until the complete regression and normal CI establish readiness. A fresh full20 arm is allowed only after both selected
questions pass.

## Passing pair and complete regression snapshot, 19:43 UTC

Trial06 scored2/2 with the identical trial05 template, Pro HIGH reader/judge,
and the compact catalog. Evaluator charge was USD0.0517141100; no question or
judge failure occurred. Both actual model-role counts are two. Native GCS and
independent download verification of all six archives completed19:43:25 UTC,
including original images/readings, unchanged source state and comparison traces.
This single comparison does not isolate stochastic answer trajectories from the
catalog change; it is a development trigger, not a causal accuracy claim.

Full06 began19:43:38 UTC with eight answer workers, the exact tested template
and renderer behavior, both Pro HIGH roles and a USD2.50 evaluator admission
stop. All20 answers are newly generated, with no carryover from the passing
pair. The runtime remains the immutable tested aec35494f revision; commit
efe52d676 makes that same explicit template canonical without changing its
reader behavior. The current canonical empty-trace prompt is32564 characters;
135833 repeated schema characters are still removed when holding that template
fixed. The earlier165157/29324 comparison held the previous template fixed.

Final results, actual cost, complete generated answers and verified GCS receipts
belong to cloud PR959's `experiments/locomo-targeted-development-20261111/`
workbook and trials/full06. The snapshot here establishes no complete20 result
or held-out benchmark claim. Paid ingestion, image conversion and Sol remain
outside this authorized answer-only experiment.

## Full19 and a conservative completeness follow-up

Full06 completed19/20 with20 newly generated Pro HIGH answers and20 Pro HIGH
grades, no question failures, and64 successful tool calls. No negative or
database-budget/timeout result was found; there were no abstentions. Both
original target questions are CORRECT. Evaluator meter USD0.4355390650;
complete native archive and independent readback finished20:02:24 UTC.

The new marked miss omits one source-supported state from a list. The earlier
Luna judge accepted a shorter list. Trial07 tested a universal source-to-attribute
paragraph, but left that omission unchanged and lost the book question (2/3).
It is rejected, not appended to the current template. The same Pro model also
accepted an equivalent partial emotion list in07 that it rejected in full06;
retain those labels as evidence of grading variation instead of relabelling them
or treating a lucky verdict as restored coverage.

Candidate08 returns to the full19 template and adds a rule limited to emotional
recall: a person's own clauses can convey an attitude without an explicit state
noun; the time of that attitude can differ from the future event it concerns.
Translate the supported attitude into the requested type of value while keeping
the person's identity, subject and time scope. This contains no state names,
people, work titles, reference labels or expected answers. It neither guarantees
an attitude from an event alone nor expands the answer to later changed reactions.
It is an ordinary language interpretation rule, not a new extraction/scoring path.

The rejected alternative was a broader unconditional attribute rule. Expanding
the candidate search, re-ingesting or changing the judge would introduce different
variables after sufficient source evidence already arrived.08 holds the processed
source, renderer, models and judge wording fixed and tests the marked question
with the original two targets. At this snapshot it is running; inspect actual
answer coverage as well as labels before adopting it or starting a new full20.
See cloud PR959 for exact prompts, unmodified states, costs and GCS proofs.

Candidate08 observed3/3 CORRECT under Pro HIGH for both roles. The answer now
expresses the previously omitted positive attitude, with negative-state synonyms
retained; the original two targets also pass. This is stronger semantic coverage
than07's lucky partial-list grade, not evidence that all future model draws will
match. No expected state name or benchmark-specific value was added. Native GCS
verification is pending at this snapshot, then a fresh complete20 regression
must regenerate every answer and grade. Adopt08's class-scoped instruction in
the draft current template, without retaining07's broader failed paragraph.

## Final development results and selected template

The selected current template is08, SHA256
`3b9591e5d13763488098ee313e1bbf4153f95f1c6fa0606895c066b3e25670f2`.
The targeted cohort passed3/3, including the two original questions and the
additional emotional-recall omission. No expected condition, emotion, theme,
person, title or reference ID was added to the new instructions.

| Complete arm | Answer workers | Correct | Technical failures | Evaluator USD |
| --- | --- | --- | --- | --- |
| full06 |8|19/20|0|0.4355390650|
| full08 |8|19/20|0|0.3363898325|
| full08-w16 |16|17/20|1|0.3549198300|

Every complete arm generated fresh attempts for all20 questions with Pro HIGH
reader and judge. No targeted or earlier answers were copied. All outcomes are
retained in cloud PR959; native six-archive backup receipts and independent
readback proofs are recorded there. This is development evidence, not held-out
accuracy or stable100% coverage. Both clean eight-worker runs scored19/20;
the16-worker scaling hypothesis is not adopted.

The emotional attitude is now expressed and graded CORRECT. The visual-content
answer remains unstable despite both owning image/dialogue chunks arriving.
The source contains a named non-exclusive favorite and a separately loved
unnamed collection, without establishing a unique unrestricted favorite. Some
answers describe pictured clues without making the requested thematic
conclusion. This is a synthesis/reference-selection issue and grading
sensitivity, not proof that the annotation is wrong or evidence of a retrieval
loss in those audited runs.09's general qualified-theme rule scored0/1 despite
complete retrieved context; it is rejected. No more specific steering is added.

The final restriction answer names a source-qualified condition without naming
its affected concrete item and is graded WRONG. Earlier complete answers named
both. This supports checking answer coverage and grade variation together,
rather than declaring the problem universally solved from a targeted pass.
The final17/20 therefore contains two answer/grade misses and one technical
failure. Do not merge those results with targeted successes into a new20/20.

## Observed parallel read admission limit

The16-worker replication encountered one HTTP500 from `adjacent_chunks` and
three typed empty boundary results. The API had a dedicated read pool and
concurrency limit of4, a one-second admission wait, and a distinct120-second
query deadline. The traceback reaches `BoundedPostgresReadPool` through
`adjacent_chunks -> _confirm_chunks -> chunk_texts` and ends with
`PostgreSQL read concurrency limit reached before admission`. The boundary
calls also returned after about1.015seconds. This is admission pressure, not
proof that their SQL ran for120seconds or that stored evidence disappeared.

An identical adjacent-chunk GET at low load returned200 with two chunks in87ms,
without a model call, settings change, ingestion or replacement answer. The
sanitized settings, traceback path and probe receipt are retained in
`trials/full08-w16/capacity-observation.json` in cloud PR959 and the native run
archive. The earlier eight-worker comparisons had no failed or boundary tool
results; retain that operational setting for this source. Do not assume the
harness's supported worker maximum is the serving API's admitted capacity.

Two general follow-ups are justified by this observation, but are not shipped
by this reader-only PR: size bounded server admission and wait configuration
alongside client concurrency; and make adjacent-chunk admission exhaustion a
retryable typed boundary instead of an uncaught HTTP500. Keep resource limits
explicit rather than removing admission controls. Do not expand this prompt
change into a new serving implementation without its own tests and design check.

The standalone09 auditor also required a technical correction because current
serial identities omit default worker count1. The corrected effective-default
check still validates all fingerprints, model roles and archived bytes. Its
failed log and successful retry remain in cloud PR959. No model, answer, grade
or ingestion was repeated for that audit repair.

No further semantic candidate or unchanged replication is selected in this
attempt. The full10-conversation campaign is not launched. The retained store,
all unsuccessful trials, actual costs and complete answer/tool/judge states
remain available for a separately justified next experiment.


## Explicit current-judge replay of a retained source (2026-11-12)

The retained conv47 source has the same ingestion profile, generations, models,
schemas and loop settings as the current full reader. Its saved judge wording
predates the current canonical wording. Rejecting all replay would require
re-ingestion even though grading has no effect on stored claims or facts; silently
ignoring this difference would hide an evaluation-policy change. The comparison
runner therefore accepts an explicit `--use-current-judge-prompt` opt-in. It
selects only the current canonical wording, records the choice and effective
prompt hash in the new immutable identity, and keeps the old source identity,
golds and grades unchanged. There is no arbitrary template loader or historical
executable protocol. Schema, effort, provider, ingestion and readiness checks
remain enforced; other drift still fails. Existing commands retain their
identities and strict default. Changing the opt-in on resume is rejected.

This adds no model call, ingestion cost, network surface or paid-reuse boundary.
It makes current-policy re-scoring explicit rather than treating an old score
policy as an ingestion requirement. The new conv47 arm uses the same general
reader template and Pro HIGH model roles as conv42, with eight answer workers.
It is a retained-data answer/scoring comparison, not fresh ingestion validation.
