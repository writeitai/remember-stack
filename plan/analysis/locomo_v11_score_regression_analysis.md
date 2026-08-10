# RS-LoCoMo-Full-v11 score regression analysis

**Status:** non-binding analysis and proposed work queue, 2026-08-10
**Run:** `RS-LoCoMo-Full-v11` at repository revision
`213551c7ddeafa0304ed50940d523971da9f5193`
**Current-code check:** the benchmark and retrieval code is unchanged on
`main` at `c6f263c9`, so the answer-agent findings still apply. Main also has
D86's later E3 unknown-entity-type behavior and generation bump, so 63.57% is
the score of the tested revision—not a new exact score for current `main`.

## Executive summary

The fresh V11 run scored **979/1540 = 63.57% judge accuracy** and
**0.5417 official LoCoMo F1**. The best durable earlier full run over the same
1540 question IDs scored **1100/1540 = 71.43%**. The remembered “about 73%” is
directionally right, but the exact recoverable baseline is 71.43%.

The 121-answer regression is not evidence that the full memory was broadly
ingested incorrectly, nor that base `question_context` became worse. The
strongest observed signal is answer-agent routing and retrieval affordance:

| V11 route | Questions | Earlier score on the same IDs | V11 score | Change |
| --- | ---: | ---: | ---: | ---: |
| `question_context` | 1124 | 832 (74.02%) | 840 (74.73%) | **+8** |
| `current_context` | 393 | 254 (64.63%) | 139 (35.37%) | **-115** |
| No tool: malformed model output | 23 | 14 (60.87%) | 0 | **-14** |
| **Total** | **1540** | **1100 (71.43%)** | **979 (63.57%)** | **-121** |

On the questions for which V11 selected `question_context`, the V11 outcomes
were eight better than V8-strong. The answer agent sent 393 other questions
through `current_context`, including historical, temporal, list, and multi-hop
questions for which that operation is often the wrong grain; that same subset
was 115 answers worse. The 23 pre-tool structured-output failures co-occur with
another 14 losses. These subsets reconcile the complete drop exactly, but they
are observational: a forced-route replay on the same stores is still needed to
measure the counterfactual recovery.

The likely mechanism is not just “the model misunderstood one description.”
V8-strong told Luna to use `question_context` first for ordinary questions and
required it before `Unknown`. V11 replaced that with “choose the cheapest
suitable path,” added a high-salience `current_context` assured operation, and
exposed 19 other paths. A minimal-effort agent that made exactly one retrieval
call then treated a current-fact operation as a general question-answering
operation.

The answer agent technically had the complete 22-tool read plane, including
P1, P2, P3, SQL, Cypher, primitives, and saved queries. In practice it made
exactly one retrieval call for every successfully answered question and used
only two tools: `question_context` and `current_context`. It never used P2, P3,
SQL, Cypher, a direct primitive, or a saved query. V11 therefore proves that
the full plane was *reachable*, not that the answer agent used it effectively.

The smallest sensible response is:

1. restore evidence-first prompt priority for ordinary questions while leaving
   all 22 paths freely selectable when their intent fits;
2. teach the agent the semantic distinction between current-state and
   assertion-history questions;
3. ablate base `question_context` against its optional enrichments one variable
   at a time;
4. teach the answer reader to normalize relative time against visible source
   timestamps; and
5. test the already-built one-call multi-hop compound path before adding any
   multi-call planning.

Do not redesign ingestion or restore the 17 retired adapters on this evidence.
Run a cheap, question-level replay/ablation first. Only then run another paid
1540-question publication score.

## 1. What was measured

### 1.1 V11 publication run

The run used:

- protocol `RS-LoCoMo-Full-v11`;
- dataset SHA-256
  `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4`;
- repository revision `213551c7ddeafa0304ed50940d523971da9f5193`;
- `openai/gpt-5.6-luna` at reasoning effort `none` for both the answer agent and
  judge;
- temperature 0 and one judge repetition;
- fresh isolated ingestion for all ten conversations; and
- the 22-tool public read catalog bound by
  `plan/designs/locomo_benchmark_design.md` §§2, 6, and 7 and `decisions.md`
  D85.

The merged durable summary is on the benchmark VM at:

```text
/opt/remember-stack-v11-213551c7/.benchmark-runs/
  locomo-v11-merged-213551c7/summary.json
```

The manifest is complete: 1540 unique expected question IDs, no duplicates,
and no missing records. Twenty-three records are explicit answer-reader
failures, not silently absent questions.

### 1.2 Earlier baseline

The recoverable earlier `RS-LoCoMo-Full-v8-strong` result is split across two
disjoint durable summaries:

```text
/opt/remember-stack/.benchmark-runs/shard-m1       986/1390
/opt/remember-stack/.benchmark-runs/rescore-v8     114/150
```

Together they contain each of the same 1540 question IDs exactly once and
score 1100/1540 = 71.43%. V8-strong used revision
`0ef54549728b3ae16691f4f5b467948ce550c077`, the same Luna answer and judge
models, reasoning effort `none`, temperature 0, and the same judge prompt and
schema hashes.

This comparison is diagnostic, not publication-equivalent: V8 and V11 have
different retrieval catalogs and answer prompts. It is nevertheless unusually
useful because the dataset, question IDs, answer model, judge model, and judge
contract are aligned. Comparing the same IDs by V11's chosen first tool locates
where outcome changes co-occurred and defines the counterfactual replay.

### 1.3 Category labels

LoCoMo's numeric labels are easy to misread because the public paper prose,
dataset numbers, and scorer display order are not self-explanatory. For this
report the official dataset/scorer mapping is:

| Category | Meaning |
| ---: | --- |
| 1 | Multi-hop |
| 2 | Temporal |
| 3 | Open-domain knowledge / commonsense |
| 4 | Single-hop |
| 5 | Adversarial; excluded by the RememberStack protocol |

The same mapping is used in
`design/benchmarks/findings-2026-07-31.md`. External sources checked on
2026-08-10: the [LoCoMo paper](https://aclanthology.org/2024.acl-long.747/),
the [official repository](https://github.com/snap-research/locomo), its
[`evaluation_stats.py`](https://github.com/snap-research/locomo/blob/main/task_eval/evaluation_stats.py),
and the repository's [category-label clarification issue](https://github.com/snap-research/locomo/issues/6).
The GitHub issue records the ambiguity; the paper and scorer are the authority.

## 2. Results

### 2.1 Headline and categories

| Category | Earlier V8-strong | V11 | Correct-answer change | Percentage-point change |
| --- | ---: | ---: | ---: | ---: |
| 1 multi-hop | 135/282 (47.87%) | 104/282 (36.88%) | -31 | -10.99 |
| 2 temporal | 234/321 (72.90%) | 178/321 (55.45%) | -56 | -17.45 |
| 3 open/commonsense | 31/96 (32.29%) | 33/96 (34.38%) | +2 | +2.08 |
| 4 single-hop | 700/841 (83.23%) | 664/841 (78.95%) | -36 | -4.28 |
| **Total** | **1100/1540 (71.43%)** | **979/1540 (63.57%)** | **-121** | **-7.86** |

The answer-level transition matrix is:

| Earlier V8-strong | V11 correct | V11 wrong |
| --- | ---: | ---: |
| Correct | 856 | 244 |
| Wrong | 123 | 317 |

This is not merely a fixed set of old misses. V11 recovered 123 previously
wrong answers, but lost 244 previously correct ones.

The complete category-by-route view is:

| Category and V11 route | Questions | V8-strong correct | V11 correct | Change |
| --- | ---: | ---: | ---: | ---: |
| Multi-hop — `question_context` | 148 | 72 (48.65%) | 75 (50.68%) | +3 |
| Multi-hop — `current_context` | 129 | 62 (48.06%) | 29 (22.48%) | -33 |
| Multi-hop — no tool | 5 | 1 (20.00%) | 0 | -1 |
| Temporal — `question_context` | 233 | 169 (72.53%) | 156 (66.95%) | -13 |
| Temporal — `current_context` | 87 | 65 (74.71%) | 22 (25.29%) | -43 |
| Temporal — no tool | 1 | 0 | 0 | 0 |
| Open-domain — `question_context` | 50 | 17 (34.00%) | 20 (40.00%) | +3 |
| Open-domain — `current_context` | 43 | 13 (30.23%) | 13 (30.23%) | 0 |
| Open-domain — no tool | 3 | 1 (33.33%) | 0 | -1 |
| Single-hop — `question_context` | 693 | 574 (82.83%) | 589 (84.99%) | +15 |
| Single-hop — `current_context` | 134 | 114 (85.07%) | 75 (55.97%) | -39 |
| Single-hop — no tool | 14 | 12 (85.71%) | 0 | -12 |

This cross-tab strengthens the routing hypothesis beyond multi-hop and
temporal: the `current_context` subsets account for net losses in multi-hop,
temporal, and single-hop, while the `question_context` subsets improve in three
of four categories. Temporal remains the important exception: its
`question_context` subset also lost 13 answers, so routing cannot explain that
category alone.

### 2.2 Actual tool behavior

Across 1540 questions:

- 1124 called `question_context`;
- 393 called `current_context`;
- 23 failed before any tool call because Luna did not produce the required
  JSON step after the allowed retries;
- successful items made exactly one tool call each; and
- there were zero calls to the other 20 catalog entries.

The run made 3251 answer-agent model calls and 1517 tool calls. It made 1517
judge calls because the 23 reader failures were deterministically scored zero.
Recorded evaluator cost was **$6.237223640**; ingestion cost is excluded because
it lives in the deployment ledger rather than the benchmark summary.

This behavior repeats the measured constraint behind
`plan/designs/agent_retrieval_surface_design.md` §2: common intents need
one-call ergonomics because answer agents usually spend about two model calls
per question. Giving the model 22 available tools did not cause it to discover
schemas, plan SQL, traverse P2, inspect P3, or compose multiple reads. The
generic plane remained an escape hatch that the minimal-effort agent never
used.

## 3. `question_context` versus `current_context`

This distinction is central to interpreting the run.

### 3.1 `question_context`: source evidence for answering a question

`question_context` is the evidence-first, high-recall operation. Its default
path combines semantic and BM25 nominations over both claims and live source
chunks, confirms them against the live PostgreSQL visibility authorities, and
returns the source text, evidence, timestamps, and provenance needed to answer
ordinary questions. Its public grain is **evidence**: “what did the source
say, and where/when did it say it?”

It is appropriate for:

- events and historical questions;
- temporal questions, including relative dates;
- lists and multi-fact answers;
- testimony and source wording; and
- ordinary recall when the caller does not yet know which fact/entity path it
  needs.

`question_context` v4 also has `include_facts` and `include_entities` flags,
both defaulting to false. Facts reuse `current_context` internally; entities
add resolution/nomination. The base evidence path does not need either. See
`plan/implementation_notes/open_query_space_batch_d.md`, “`question_context`
v4 reuses existing authorities,” and `decisions.md` D81/D83.

### 3.2 `current_context`: adjudicated facts that hold now

`current_context` starts from semantic nominations over the P1 fact labels
(relations and observations), then has PostgreSQL confirm which nominated
facts are currently believed and valid at the query instant. It returns fact
labels plus bounded supporting/contradicting evidence. Its public grain is
**fact**: “what does the system currently hold true?”

It is appropriate for questions such as:

- “Where does Ana work now?”
- “Is Sam still living in Prague?”
- “What is the latest known status of the project?”

It is not a transcript/history search. It has no source-chunk channel, does
not aim to collect every facet of a list, and may normalize a past event into
a compact current fact label. Its `k` is at most 30 and its evidence is bounded
per fact. See
`plan/implementation_notes/agent_retrieval_surface_batch_c.md`, “Nomination
and confirmation” and “Evidence selection and totals.”

### 3.3 Plain-language example

Suppose the memory says:

> On 15 April 2022, Nate said he dyed his hair purple last week.

`question_context` is intended to retrieve that dated source passage so an
answer reader can calculate **the week before 15 April 2022**.

`current_context` may instead return a normalized fact label such as **“Nate
dyed his hair purple last week”** with supporting evidence. That fact is useful
for deciding whether the event is presently believed, but the compact label is
not sufficient historical context by itself. In the actual V11 trace, the
supporting claim carried `claim_valid_from=2022-04-08`, but the fact validity
was null and Luna answered “Last week.”

In one sentence: **`question_context` retrieves the source material needed to
answer a question; `current_context` retrieves the system's adjudicated view of
what holds now.** They overlap because current facts carry evidence, but they
are not substitutes.

## 4. Strongest explanation of the score drop

### 4.1 Primary hypothesis: prompt/catalog affordance led to wrong-grain choices

The route-partitioned comparison is internally exact:

- On the 1124 V11 `question_context` questions, V11 improved by eight correct
  answers over V8-strong.
- On the 393 V11 `current_context` questions, V11 lost 115 correct answers.
- The 23 structured-output failures lost 14 answers that V8-strong had answered
  correctly.

These three numbers sum exactly to the total regression: `+8 - 115 - 14 =
-121`.

It is not a randomized or forced-route experiment. V8-strong and V11 used
different stores, prompts, catalogs, retrieval implementations, and answer-cap
instructions, so the table establishes co-occurrence rather than the exact
number a reroute would recover.

It nevertheless identifies the strongest hypothesis. V8-strong said “use
`question_context` first for ordinary questions” and required that call before
`Unknown`. V11 said “choose the cheapest suitable path,” made
`current_context` a prominent assured operation, and allowed any content tool
before `Unknown`. Luna at effort `none` then selected it for past events,
dates, lists, multi-hop questions, and open-domain inference. The model often
treated “facts about X” as equivalent to “context needed to answer this
question.” They are not equivalent in this architecture.

The confirmation test is a same-store, same-reader forced-route replay, not a
new full ingestion. Until that replay, this report uses “routing hypothesis”
rather than claiming 115 answers are causally recoverable.

### 4.2 The complete retrieval plane was access, not effective use

The full-plane change in `decisions.md` D85 was implemented: P1 was reachable
through assured operations, primitives, and SQL; P2 through Cypher; P3 through
its ordinary mount; and saved queries were discoverable. Nothing prohibited
the answer agent from using them.

The agent nevertheless selected exactly one of two assured operations on every
successful question. It did not even call discovery before SQL/Cypher, despite
the prompt explaining that path. Therefore:

- P1 was used indirectly through the two assured operations;
- P2 was built and available but unused;
- P3 was built, mounted, and available but unused; and
- SQL, Cypher, primitives, and saved-query patterns were available but unused.

This is an affordance/planning failure, not an access-control failure. A broad
catalog cannot compensate for a weak first-tool decision when the agent
usually makes only one retrieval call.

### 4.3 The 23 format failures are real but secondary

Twenty-three questions exhausted the allowed pre-tool retries because Luna did
not return a valid `AnswerAgentStep`. They were scored wrong, as the protocol
requires. Fourteen of those question IDs were correct under V8-strong. Recovering all
23 would add at most 1.49 percentage points; it cannot explain the 7.86-point
drop.

### 4.4 Observed paired judge disagreement is too small to explain the drop

The old and new runs used the same judge model, prompt, schema, temperature,
and repetition count. There were 638 questions with identical normalized
generated answers across the two runs. Thirteen received different judge
labels: nine correct-to-wrong and four wrong-to-correct, a net loss of five
answers (0.32 percentage points).

One judge repetition therefore adds visible noise. This paired subset is not a
random sample and does not establish a general variance rate, but its net five
answers cannot explain a 121-answer drop. Repeated fixed-answer judging is
needed before publishing a confidence band.

## 5. Retrieval diagnostics

### 5.1 Gold source-turn coverage

A deterministic diagnostic matched retrieved source text against the LoCoMo
gold evidence turns. It is approximate—it uses rendered source text overlap,
not semantic entailment—but it clearly separates the two routes.

Thirteen category-3 questions have no scorable gold turn. Overall coverage
therefore uses 1527 questions; outcome accuracy still uses the full 1540.

| Route | Questions | At least one gold turn | All gold turns | Answer accuracy |
| --- | ---: | ---: | ---: | ---: |
| V11 `question_context` | 1124 | 1092/1124 (97.15%) | 1050/1124 (93.42%) | 840/1124 (74.73%) |
| V11 `current_context` | 393 | 275/393 (69.97%) | 170/393 (43.26%) | 139/393 (35.37%) |
| V8-strong overall | 1527 scorable | 1439/1527 (94.24%) | 1387/1527 (90.83%) | 1100/1540 (71.43%) |
| V11 overall | 1527 scorable | 1367/1527 (89.52%) | 1220/1527 (79.90%) | 979/1540 (63.57%) |

For `question_context`, accuracy was 78.10% when all gold turns were present
and 27.03% when they were not. For `current_context`, accuracy was 57.06% with
full gold coverage and 18.83% without it. Better evidence coverage helps both,
but `current_context` also leaves more work to a reader that sees compact fact
labels rather than the most useful source context.

### 5.2 `question_context` call shape

The 1124 calls were not uniform:

- `k`: 20 on 475 calls, 50 on 327, 10 on 308, default on 14;
- `candidate_k`: 100 on 494, 200 on 342, default on 243, 50 on 45;
- `include_facts`: true on 727, false on 238, omitted on 159; and
- `include_entities`: true on 771, false on 189, omitted on 164.

The answer agent frequently enabled both optional enrichments even though they
default false. A typical enriched response carried roughly 20 chunks, 38
evidence records, 20 facts, and 20 entities. `include_facts=true` internally
invokes the same current-fact machinery implicated above. Therefore the
`question_context` arm is not a clean base-operation experiment: 727 calls
also included facts and 771 included entities.

V11's overall tool responses were smaller than V8-strong's (median roughly 56 KiB
versus 142 KiB), but smaller is not automatically clearer. The richer mix of
chunks, claims, facts, and entities gives a low-effort reader more competing
answer candidates. That is a hypothesis, not a measured flag effect. The next
ablation should compare base `question_context` against each enrichment one at
a time.

### 5.3 Latency

Median retrieval latency on the three V11 VMs settled around 30, 36, and 38
seconds per call; the earlier V8-strong median was about 3.7 seconds. Optional
fact enrichment adds current-fact nomination and confirmation, but its causal
share is unmeasured and VM/store/query differences are confounded. The run was
structurally slower even though each question used only one retrieval tool.

Correctness comes first. Testing the declared default-false base path is the
lowest-risk clarity/latency ablation and removes neither feature.

## 6. Why multi-hop did not improve

Multi-hop fell from 47.87% to 36.88%, but the category total hides two very
different routes:

| Multi-hop route | Questions | Earlier V8-strong | V11 |
| --- | ---: | ---: | ---: |
| `question_context` | 148 | 48.65% | **50.68%** |
| `current_context` | 129 | 48.06% | **22.48%** |
| No tool | 5 | 20.00% | 0% |

On the `question_context` subset, V11 slightly improved, although most calls
also enabled optional facts/entities. Its measured issue is completeness: it
found at least one gold turn for 97.30% of these questions, but all required
gold turns for only 77.03%. A multi-evidence or list answer often needs several
sessions or facets. This result does not by itself prove that a graph traversal
is required.

`current_context` is even less suitable: it ranks fact labels independently
and returns a bounded selection. For “What are Joanna's hobbies?”, it returned
reading, writing, and exploring nature while missing movies and spending time
with friends. The facts it found were plausible but the requested set was
incomplete.

The repository still contains multi-hop query capability:
`multi_hop_context` exists as a demoted engine/recipe definition and a saved
query pattern is discoverable, while P2 can be queried through Cypher. It was
not one of V11's 22 direct tools. D83 deliberately removed it from the three
canonical assured operations rather than preserving 17 pre-release adapters.
V11's generic agent never discovered or used the saved-query/Cypher paths.

The simplest first experiment is not a general multi-call planner. Pin base
`question_context` arguments, compare its completeness with the already-built
compound `multi_hop_context` implementation on the category-1 slice, and only
then consider re-promoting exactly that one operation. Re-promotion would be an
explicit partial amendment of D83, not restoration of hidden behavior.

## 7. Why temporal did not improve

Temporal fell from 72.90% to 55.45%, the largest category loss:

| Temporal route | Questions | Earlier V8-strong | V11 |
| --- | ---: | ---: | ---: |
| `question_context` | 233 | 72.53% | **66.95%** |
| `current_context` | 87 | 74.71% | **25.29%** |
| No tool | 1 | 0% | 0% |

There are two separate problems.

### 7.1 The `current_context` temporal subset accounts for 43 net losses

`current_context`'s compact current-fact representation is poorly matched to
questions asking when a past event occurred. In the purple-hair example, the
source and claim contained enough information to resolve the date, but the
answer reader surfaced the relative phrase from the fact label instead.

The subset fell from 65 to 22 correct. This makes wrong-grain choice the
strongest explanation for 43 temporal losses, but the same-store forced-route
replay is needed to establish how many are recoverable. The purple-hair trace
also demonstrates reader-attention failure because its supporting evidence did
carry an absolute claim time.

### 7.2 The evidence-first route still has a reader problem

For temporal questions routed to `question_context`, all gold turns were
present 97.42% of the time, yet answer accuracy was only 66.95%. Trace examples
include:

- a May 2022 adoption date present in the raw source chunk but answered
  `Unknown`;
- a “last week” event with the dated session present, but returned as the
  unresolved relative phrase; and
- the correct dated script evidence present alongside extra facts/entities,
  with the reader choosing the wrong June date.

Here retrieval recall is already high. Evidence consumption is the leading
hypothesis: the existing prompt already says to resolve relative dates, but
the instruction did not reliably win over relative phrases or competing dates.
Timestamp co-location, absolute-normalization guidance, enrichment removal,
and reasoning effort must be ablated separately rather than assumed fixes.

## 8. Is ingestion at fault?

There is no evidence of a broad ingestion collapse:

- all ten conversations completed fresh isolated ingestion;
- `question_context` achieved 93.42% full gold-turn coverage on its chosen
  questions;
- live source chunks retained the session text and timestamps; and
- in the purple-hair trace, Claimify produced an exact
  `claim_valid_from=2022-04-08` anchor.

However, two focused ingestion/representation questions remain actionable:

1. Why was the derived fact's validity null when its supporting claim had an
   exact valid time?
2. Do relation/observation labels preserve enough facets for complete list
   answers, or do they fragment/drop useful attributes before retrieval?

D84's chunk-level extraction change (`decisions.md` D84 and
`plan/designs/chunk_level_extract_design.md`) changes throughput and work
granularity, not the intended extraction semantics. It is still a
cross-revision confound because V8-strong and V11 used different fresh
ingestions; it should not be credited or blamed for quality without a
stage-by-stage trace. D86 then changed E3 again after this run, which is why the
score cannot be relabeled as an exact score of current `main` without another
fingerprinted run.

Before changing ingest, take one temporal-heavy and one multi-hop-heavy
conversation and trace a small set of misses through:

```text
gold source turn
  -> indexed source chunk
  -> extracted claim + temporal fields
  -> normalized relation/observation
  -> evidence link and current status
  -> retrieval nomination/confirmation
  -> answer trace
```

If the gold text and claim are present but the answer fails, fix retrieval or
the reader. If the claim loses its time/facet before retrieval, then make the
smallest ingest correction demonstrated by that trace.

## 9. Proposed work queue

These are proposals pending owner acceptance, not the current binding design.
They deliberately avoid restoring a large compatibility surface or inventing a
new planner framework. Every scored diagnostic that changes prompt, routing,
catalog, model, or ingest needs its own fingerprint, even before a full run.

### P0 — fix and replay before another full paid run

1. **Measure the counterfactual route first.** On the 393 V11
   `current_context` IDs, replay base `question_context` against the same stores
   and reader settings. This tests the primary hypothesis without re-ingestion
   or bundling several prompt changes.
2. **Ablate enrichments independently.** On the existing
   `question_context` IDs, compare base flags, facts only, and entities only at
   pinned `k`/`candidate_k`. Measure accuracy, coverage, response size, and
   latency for each.
3. **Restore evidence-first prompt priority, not a harness gate.** Use V8-style
   guidance that ordinary/history questions normally start with
   `question_context`, while preserving the agent's ability to choose any of
   the 22 D85 paths first when its semantic intent fits. `current_context`
   remains correct for current-state questions even when they do not literally
   contain “now” or “latest.”
4. **Ablate temporal presentation separately.** Co-locate source/session time,
   source text, and `claim_valid_from`, then separately test an instruction to
   return an absolute date/interval whenever computable.
5. **Repair structured-output handling.** Test the provider's strongest native
   structured/tool-call mode or one bounded deterministic repair path so a
   non-JSON first step does not throw away a whole question.

Use the already-ingested stores if they still exist; otherwise use a fixed
diagnostic slice. Change one variable per ablation. Do not invoke the judge for
initial retrieval checks: compare generated answers and evidence metrics first,
then judge only promising variants.

### P1 — targeted multi-hop and temporal improvements

6. **Test one-call multi-hop options.** On the 282 multi-hop items, compare
   pinned base `question_context` with the already-built compound
   `multi_hop_context` implementation and its saved-query/P2 equivalent. If the
   compound path materially wins, propose re-promoting exactly that operation;
   do not restore 17 adapters or add a general multi-call planner.
7. **Separate list completeness from graph traversal.** Score set/list
   completeness separately and test retrieval diversity before concluding that
   every category-1 miss needs P2.
8. **Test reader reasoning on the temporal slice.** Try Luna at low or medium
   reasoning effort on only the 321 temporal questions, with identical
   retrieval. This isolates answer synthesis from retrieval and ingest.
9. **Trace two conversations through ingest now.** The null fact-validity versus
   exact claim-validity example is already enough to justify a small temporal
   trace, plus one multi-facet/list trace. Do not wait for a broad retrieval
   failure and do not re-ingest all ten.

### P2 — measure ingestion and evaluator uncertainty

10. **Act only on demonstrated ingest loss.** If the two traces show
    claim-to-fact validity or facet loss, make the smallest correction and
    replay those conversations before broad re-ingestion.
11. **Quantify judge variance for publication.** Rejudge identical fixed
    answers two additional times or publish a small confidence/sensitivity
    band. Do not treat judge tuning as a memory improvement.

## 10. Validation gates

Use a short ladder so iteration stays cheap:

1. A hand-checked set of the concrete failures above.
2. A fixed stratified slice: current-state, historical temporal, temporal
   relative-date, multi-hop, list, open, and ordinary single-hop.
3. The full 282 multi-hop, 321 temporal, and a fixed single-hop control subset,
   reusing stored ingestion.
4. Only after a clear subset win, one complete fingerprinted 1540-question
   publication run from an exact `main` revision.

For every candidate report:

- answer accuracy and official F1;
- route/tool-call distribution;
- no-tool/format failures;
- all-gold-turn and any-gold-turn evidence coverage;
- accuracy conditional on full gold evidence;
- temporal absolute-normalization success;
- list/multi-hop completeness;
- response size and retrieval latency; and
- answer/judge cost separately from ingestion.

The success condition is not merely “the model can access P2/P3.” It is that
the measured answer behavior uses the right retrieval grain and improves the
target categories without regressing single-hop.

## 11. What not to do yet

- Do not restore all 17 retired recipe adapters. There are still no
  compatibility users, and this run does not justify that scope.
- Do not add a general natural-language-to-SQL/Cypher planner. The current
  failure happens before such complexity would help.
- Do not force every question through P2/P3. They are useful channels, not a
  ceremony.
- Do not increase `k` globally before the pinned-depth ablation. Temporal
  already has very high gold-turn coverage on `question_context`.
- Do not redesign Claimify or re-ingest all ten conversations until the two
  forensic traces identify an ingest-stage loss.
- Do not optimize the 30–38 second retrieval median at the expense of first
  fixing route correctness. Turning off unnecessary enrichments is the first
  low-risk latency experiment.

## 12. Bottom line

The tested revision scored 63.57%; current `main` needs a new fingerprinted run
because D86 changed E3 afterward. Nothing in this result shows that the full
retrieval plane is inherently worse. It shows that V11 behaved as a two-tool,
one-retrieval-call system despite having 22 tools, and that the 393
`current_context` choices co-occur with 115 of the net losses. On the 1124
`question_context` choices—most of them enriched with facts/entities—outcomes
were eight better in aggregate. A same-store forced-route replay is the
causality gate.

The multi-hop result is consistent with one retrieval finding only part of the
required evidence while the agent never used the available graph/query paths;
the data does not yet prove which misses require graph structure. Temporal
regressed through both a 43-answer loss on the `current_context` subset and a
13-answer loss on the `question_context` subset. The latter had very high gold
coverage, pointing to reader/date selection or representation rather than
missing source turns.

The next move should be independent, fingerprinted route/enrichment/temporal
ablations plus two narrow ingest traces—not another broad architecture change
and not another blind full re-ingest.
