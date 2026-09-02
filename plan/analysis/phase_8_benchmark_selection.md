# WP-8.1 — Benchmark Landscape and Selection

**Survey date:** 2026-07-23; amended 2026-09-01 for Workspace-Bench
**Status:** selected for WP-8.2 and WP-8.2W adapter work
**Scope:** external, comparative benchmarks. The internal D22 golden sets and S1–S63 contract
battery remain separate.

## Decision

Start with five bounded benchmark surfaces:

1. **LoCoMo question answering** as the recognizable, relatively inexpensive conversational
   memory headline.
2. **LongMemEval-S** as the harder standard diagnostic for multi-session reasoning, temporal
   reasoning, knowledge updates, and abstention.
3. **MemoryAgentBench FactConsolidation-SH/MH** as the focused external test of current-state
   selection after conflicting updates.
4. **MultiHop-RAG retrieval** as the focused cross-document and temporal multi-hop test, including
   null queries.
5. **Workspace-Bench-Lite** as the heterogeneous-file and dependency-aware workspace test, with a
   direct ingestion audit and a matched native-agent versus agent-plus-RememberStack comparison.

This is deliberately not a universal benchmark framework. WP-8.2 should add thin adapters for
the memory-backend protocols, two simple retrieval floors, and two OSS competitor systems.
Workspace-Bench is the one selected agent-environment surface because its 74 file extensions add
format and workspace breadth that the text-native benchmarks do not. Its official runner remains
an upstream dependency rather than becoming a third-party orchestration framework inside
RememberStack. The other deferred agent-environment benchmarks listed below remain out of scope.

None of the selected external suites tests RememberStack's full contract: bi-temporal as-of
answers, explicit
contradiction co-members, source hydration, watched-source edit/retract/delete behavior, or
hard-forget non-resurrection. WP-8.5 therefore remains necessary and small; it derives those
capability checks from the existing S-battery rather than inventing another general benchmark.

## Selection criteria

A benchmark enters the first portfolio only when it has:

- a public dataset and runnable evaluation protocol;
- enough adoption or peer-reviewed standing to make comparison useful, unless it is explicitly
  labeled as a capability track and adds unique coverage through a public reproducible protocol;
- a clean mapping to a memory-backend ingest/query boundary, or a matched control that isolates
  the added memory surface and labels the result as agent-level;
- answer or evidence labels that permit deterministic scoring, or a published judge protocol;
- a bounded development subset and a credible full-run cost;
- an attributable license and a source version that can be pinned; and
- incremental value over the other selected suites.

Selection does not waive a pre-run gate. In particular, Workspace-Bench-Lite cannot produce a
publishable run until its missing explicit dataset-license declaration is clarified or its exact
bytes are reproducibly sourced from the licensed full release.

Headline popularity alone is insufficient. Vendor-reported scores are context, not comparable
baselines, unless the exact dataset manifest, reader, judge, prompts, retrieval depth, and
failure accounting match.

## Selected benchmarks

| Benchmark | Role | Why it stays | Cost shape | Initial protocol |
|---|---|---|---|---|
| [LoCoMo](https://github.com/snap-research/locomo) | Standard conversational-memory headline | Widely used by memory vendors; ten shared histories cover single-hop, temporal, multi-hop, and open-domain QA. The authors retained ten conversations specifically for cost-effective closed-model evaluation. | Low ingest volume (roughly 90k conversation tokens), but the pinned full protocol has 1,540 answer-and-judge queries. | Ingest timestamped turns once per conversation. Include official categories 1–4: single-hop, multi-hop, temporal, and open-domain. Exclude adversarial category 5; any later result that includes it is a separately labeled protocol. Score evidence retrieval separately and use a single frozen reader and judge only for end-to-end QA. WP-8.2 commits the exact parsed question IDs and reports their count. |
| [LongMemEval](https://github.com/xiaowu0162/LongMemEval) | Harder standard memory diagnostic | 500 questions cover information extraction, multi-session reasoning, knowledge updates, temporal reasoning, and abstention; answer-session and answer-turn labels support retrieval scoring before generation. | High: every LongMemEval-S question has a separate history averaging about 115k tokens and 40 sessions. Ingest therefore grows linearly with the number of questions; it is not an inject-once/query-many corpus. LongMemEval-M is intentionally out of scope. | Use the cleaned S dataset. Include abstentions in answer accuracy; follow the official protocol and omit its 30 abstention cases only from retrieval recall because they have no answer location. Oracle histories are smoke fixtures, not headline results. |
| [MemoryAgentBench FactConsolidation](https://github.com/HUST-AI-HYZ/MemoryAgentBench) | Targeted update/conflict diagnostic | The ICLR 2026 benchmark feeds old and rewritten contradictory facts incrementally and asks single- and multi-hop questions about the final state. Its inject-once/query-many structure and deterministic substring metric avoid a judge call per item. | Medium and controllable across official 6k, 32k, 64k, and 262k context tiers. The other MemoryAgentBench competencies are not selected. | Run FactConsolidation-SH and -MH only. Report official latest-fact accuracy and, separately, whether RememberStack preserves and surfaces the superseded evidence rather than collapsing history. |
| [MultiHop-RAG](https://github.com/yixuantt/MultiHop-RAG) | Targeted graph/retrieval diagnostic | 2,556 inference, comparison, temporal, and null queries have evidence spanning zero or two to four documents. The knowledge base is only 609 news articles, and the official evidence labels support query-time evaluation without an LLM judge. | Low-to-medium: about 1.25M source tokens are ingested once; retrieval-only queries are cheap. | Make retrieval the binding comparison: evidence Recall@k, complete-evidence success, latency, and null-query precision. This is a multi-document RAG diagnostic, not the conversational-memory headline. End-to-end answer scoring is optional and runs only under an explicit cap. |
| [Workspace-Bench](https://github.com/OpenDataBox/Workspace-Bench) | Heterogeneous workspace and ingestion-economics diagnostic | Five role workspaces contain 20,476 files across 74 extensions, including office documents, PDFs, spreadsheets, presentations, code/configuration, email, images, archives, and statistical data. Tasks label essential files and dependency edges and require real output artifacts. | High: the pinned English workspace archive is about 18.7 GB, broad ingestion is paid once per immutable workspace snapshot, and the official agent plus agent-as-a-judge protocol is expensive. Lite reduces the 388 tasks to 100 and the authors estimate about 70% lower evaluation cost. | First report deterministic ingestion coverage, throughput, provider calls, tokens, and cost per extension/MIME. Then run a matched pair with the same task agent and native filesystem in both arms, adding only RememberStack's shipping public read plane in the memory arm. Use 20 fixed Lite tasks for development and all 100 Lite tasks for publication. Never ingest rubrics or gold dependency graphs. |

## Three execution tiers

WP-8.2/W must materialize the selected item IDs as committed manifests before any result is
generated. Sampling is deterministic and stratified; it is never changed after seeing scores.

| Tier | LoCoMo | LongMemEval | FactConsolidation | MultiHop-RAG | Workspace-Bench | Purpose |
|---|---|---|---|---|---|---|
| Adapter smoke | one conversation, a few questions from every retained category | ten oracle instances covering all abilities | one 6k SH and one 6k MH context | twenty queries including a null query | extension-stratified ingest audit plus five Lite tasks, one per role | Schema, ordering, scoring, and failure-path checks; no headline numbers |
| Development | 200 conversation-balanced and category-stratified questions | 40 S questions stratified across official question types and abstention | all questions in the 6k and 32k tiers | 300 queries stratified by query type and evidence count | 20 fixed, stratified Lite tasks in matched native and memory arms | Repeatable iteration with bounded spend |
| Publication | all questions in the pinned LoCoMo protocol | all 500 LongMemEval-S questions | SH and MH at all four official context tiers | all 2,556 retrieval queries; answer generation only if capped | all 100 Lite tasks in both matched arms; full 388 is a separately authorized extended run | Comparable final report, run deliberately rather than in ordinary CI |

The exact manifests, seed procedure, and dataset hashes belong to WP-8.2/W. The selection memo
binds their shapes, not IDs guessed before the adapters parse the official datasets.
LongMemEval is the exception to shared-corpus amortization: its 40-item development tier means
about 40 separate 115k-token histories, and its full tier means 500. Both require a preflight
token and monetary estimate before execution.

### Order-of-magnitude cost envelope

The envelope below counts benchmark input and evaluator calls before system-specific
transformation. It is not a price quote: BM25 and dense RAG have no LLM ingestion, while memory
systems may perform different extraction calls. WP-8.2/W converts these units to provider calls,
tokens, and currency from each pinned configuration.

| Surface | Development envelope | Publication envelope |
|---|---|---|
| LoCoMo | ingest the shared ~90k-token histories once per system; 200 retrieval, reader, and judge evaluations | same shared ingest; 1,540 retrieval, reader, and judge evaluations |
| LongMemEval-S | ingest `40 × ~115k`, or ~4.6M history tokens, per system; 40 retrieval, reader, and judge evaluations | ingest `500 × ~115k`, or ~57.5M history tokens, per system; 500 retrieval, reader, and judge evaluations |
| FactConsolidation | ingest SH and MH contexts at the ~6k and ~32k tiers; score 400 questions deterministically with no judge | ingest SH and MH contexts at all four tiers (~6k, ~32k, ~64k, and ~262k); score 800 questions with no judge |
| MultiHop-RAG | ingest the shared ~1.25M-token corpus once per system; 300 retrieval-only queries | same shared ingest; 2,556 retrieval-only queries |
| Workspace-Bench | ingest the complete pinned role workspaces once for the memory arm; 20 native-control plus 20 memory-arm task-agent runs and 40 corresponding judge evaluations | same immutable workspace build; 100 native-control plus 100 memory-arm task-agent runs and 200 corresponding judge evaluations |

For system `s`, the preflight expands this envelope as:

```text
run_cost(s) =
  ingest/build calls and tokens for s
  + retrieval calls for s
  + shared-reader calls and tokens
  + shared-judge calls and tokens
  + task-agent calls and tokens for an agent-environment surface
  + hosted/provider fees for s
```

The total run is the sum for RememberStack and the selected baselines. Development and
publication runs require explicit monetary and token ceilings; exceeding either ceiling stops
before ingestion. Full publication tiers never run in ordinary CI.

## Cost and fairness contract

Every run produces a preflight plan and refuses to start without an explicit run cap. The
preflight reports:

```text
projected total =
  one-time ingest/build calls and tokens
  + retrieval calls
  + reader calls and tokens
  + judge calls and tokens
  + task-agent calls and tokens where applicable
  + provider or hosted-baseline fees
```

The implementation follows these rules:

1. **Score retrieval first.** Evidence Recall@k, MRR/nDCG where the official labels support them,
   complete-evidence success, typed null accuracy, latency, and returned-context size require no
   answer-model or judge call.
2. **Ingest once per immutable run key.** A cache key includes system version, dataset commit,
   item manifest, model configuration, and adapter version. Changed inputs create a new run;
   caches never conceal configuration changes.
3. **Use one frozen reader and one frozen judge across systems.** Memory quality is not compared
   with different answer models. Native system answer generation may be reported separately,
   never mixed into the matched table.
4. **Prefer deterministic metrics.** FactConsolidation and MultiHop-RAG retrieval use their
   official deterministic scorers. LLM judging is reserved for the LoCoMo and LongMemEval answer
   surfaces that require semantic equivalence and Workspace-Bench's official artifact rubrics.
5. **Calibrate the judge, do not multiply it.** Double-score a fixed audit sample and report
   disagreement; do not run every expensive benchmark multiple times merely to hide evaluator
   variance.
6. **Count failures as failures.** Timeouts, parse errors, unavailable contexts, and refused
   requests remain in the denominator and are reported by class.
7. **Separate build from serve.** Record ingest wall time, model tokens, and cost separately from
   query P50/P95 latency, returned tokens, reader cost, and judge cost.
8. **Publish losses and limits.** Subset results are labeled as subset results. Vendor scores with
   different protocols appear only in contextual tables.
9. **Gate heterogeneous ingestion before agent spend.** Workspace-Bench inventories every pinned
   file, resolves converter routes, and estimates provider work before ingestion. All-file,
   all-byte, and gold-dependency coverage are reported separately; gold labels may score coverage
   only after ingestion and never select inputs.

RememberStack's own provider usage flows through `cost_ledger`. Baselines must emit the same
logical categories from their receipts or API metadata; unknown spend is reported as unknown,
never zero.

## Baseline list

The matched baseline set stays small:

| Baseline | Purpose | Boundary |
|---|---|---|
| **BM25 over benchmark-native units** | Non-neural floor and lexical sanity check | Same item manifest and reader; no hidden summarization |
| **Dense RAG over the same units** | Minimal semantic-retrieval floor | Same pinned embedder, chunk units, top-k, reader, and judge across runs |
| [**Mem0 OSS**](https://github.com/mem0ai/mem0) | Widely recognized agent-memory competitor | Pin an OSS commit and explicit local/provider configuration; hosted or paper numbers remain contextual |
| [**Graphiti OSS**](https://github.com/getzep/graphiti) | Temporal graph-memory competitor and closest structural comparison | Pin an OSS commit. Do not label it as the current hosted Zep product, and do not mix Zep's published service numbers into reproduced results |
| **Workspace-Bench native agent** | Isolate the incremental value of RememberStack on workspace tasks | Same pinned task agent, filesystem, artifact tools, prompt, resources, and judge as the memory arm; the only addition in the memory arm is the shipping RememberStack read plane |

Where an official benchmark supplies an oracle or full-context configuration, report it as a
ceiling/reader diagnostic rather than another memory system. More competitors enter only after
these four paths run end to end and a concrete missing comparison justifies the added adapter.
The BM25, dense-RAG, Mem0, and Graphiti baselines apply to the memory-backend surfaces. They are
not silently substituted for a workspace agent capable of creating the required files.

## Metrics by surface

- **LoCoMo:** evidence Recall@k and complete-evidence success where evidence IDs exist; answer
  accuracy by retained category; retrieval P50/P95; context tokens; ingest, reader, and judge cost.
- **LongMemEval:** session- and turn-level recall; answer accuracy by type including abstention;
  retrieval P50/P95; context tokens; ingest, reader, and judge cost.
- **FactConsolidation:** official SH/MH accuracy by context length; retrieval evidence success;
  current-answer correctness; a separate RememberStack-only history-preservation/contradiction
  disclosure result that is not presented as an official or matched baseline metric.
- **MultiHop-RAG:** evidence Recall@k; all-required-evidence success by evidence count; null-query
  precision/recall; results by inference/comparison/temporal/null type; retrieval P50/P95 and cost.
- **Workspace-Bench:** official rubric pass rate, task-completion thresholds, and dependency
  Node/Edge F1; direct ingestion success and failure counts by extension/MIME, all-file/all-byte/
  required-file coverage, build throughput and cost, required-file retrieval, task-agent and judge
  tokens/cost, matched-arm delta, and query P50/P95.
- **All systems:** pinned versions, item manifest, failures, build time, query latency, returned
  context size, provider calls/tokens/cost, and hardware/service topology disclosure.

No single aggregate score hides these dimensions. A small comparison table may show headline
accuracy, but the published artifact retains per-category results and the raw run manifest.

## Deferred and rejected candidates

| Candidate | Decision | Reason |
|---|---|---|
| [**Deep Memory Retrieval (DMR)**](https://arxiv.org/abs/2501.13956) | Reject | Its 500 conversations contain at most 60 messages each; the Zep study reports a 98.0% full-context baseline with `gpt-4o-mini` and argues that the task no longer distinguishes modern memory systems. |
| **HotpotQA** | Reject for the first portfolio | Standard multi-hop QA, but it is less memory-specific and less bounded than MultiHop-RAG for this purpose. |
| **LongMemEval-M** | Defer | Roughly 500 sessions per instance adds cost without a new capability category during adapter bring-up. |
| [**LongMemEval-V2**](https://github.com/xiaowu0162/LongMemEval-V2) | Defer, monitor | Current and promising, but its 451 multimodal web-agent questions reach 500 trajectories and 115M tokens per largest haystack and require reader/controller/judge infrastructure. It evaluates experienced-agent workflows rather than the initial memory-backend boundary. |
| **Full MemoryAgentBench** | Defer | The complete suite spans 103k–1.44M-token contexts, test-time learning, recommendation, summarization, and novel reasoning. FactConsolidation captures the selected update/conflict question much more cheaply. |
| [**STATE-Bench**](https://github.com/microsoft/STATE-Bench) | Defer | 450 stateful tasks, a user simulator, tool environments, and five-run reliability metrics primarily evaluate the whole agent loop; valuable later, but expensive and heavily confounded for WP-8.2. |
| [**MemoryArena**](https://github.com/ZexueHe/MemoryArena) / [**Mem2ActBench**](https://aclanthology.org/2026.acl-long.370/) | Defer | Both test memory-guided action across multi-session agent tasks. They are useful downstream product benchmarks, not simple memory-backend comparisons. |
| [**GroupMemBench**](https://arxiv.org/abs/2605.14498) | Watch | Multi-party speaker-grounded memory, knowledge updates, temporal reasoning, and abstention fit future collaboration use cases, but the benchmark is new and would add another conversational adapter before the initial suite proves itself. |
| [**LoCoMo-Plus**](https://arxiv.org/abs/2602.10715) | Watch | The new cue-to-constraint benchmark may fit authored K2 principles, but it is not yet a standard memory-backend comparison and should not reopen the removed K3 product concept. |

## Source snapshot

These are survey snapshots observed on 2026-07-23, with Workspace-Bench observed on 2026-09-01.
WP-8.2/W pins the exact dependency and data revisions in executable manifests.

| Surface | Official source | Observed revision | License note |
|---|---|---|---|
| LoCoMo | [`snap-research/locomo`](https://github.com/snap-research/locomo) | `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376` | CC BY-NC 4.0; do not vendor the dataset, and review the non-commercial restriction before commercial use |
| LongMemEval code | [`xiaowu0162/LongMemEval`](https://github.com/xiaowu0162/LongMemEval) | `9e0b455f4ef0e2ab8f2e582289761153549043fc` | MIT |
| LongMemEval data | [`xiaowu0162/longmemeval-cleaned`](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned) | `98d7416c24c778c2fee6e6f3006e7a073259d48f` | MIT |
| MemoryAgentBench code | [`HUST-AI-HYZ/MemoryAgentBench`](https://github.com/HUST-AI-HYZ/MemoryAgentBench) | `455306dcabc3842526eb83cd4e225e5d486c5c5d` | MIT |
| MemoryAgentBench data | [`ai-hyz/MemoryAgentBench`](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench) | `7ea066982b140a19337e17e60d45d4076e042faf` | MIT |
| MultiHop-RAG code | [`yixuantt/MultiHop-RAG`](https://github.com/yixuantt/MultiHop-RAG) | `cde8e844af14b3012f20158abc2854fe8458212a` | README declares ODC-BY |
| MultiHop-RAG data | [`yixuantt/MultiHopRAG`](https://huggingface.co/datasets/yixuantt/MultiHopRAG) | `71ac0d0bd1f951d2d6b70311f7d2ae404e1ffa82` | ODC-BY |
| Workspace-Bench code | [`OpenDataBox/Workspace-Bench`](https://github.com/OpenDataBox/Workspace-Bench) | `3fbd0f1a136720fece86786545983e26642c3db2` | MIT terms (copyright line names WOLF-Bench); postdates the upstream evaluator-metadata leakage fix |
| Workspace-Bench full task data | [`Workspace-Bench/Workspace-Bench`](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench) | `3491f9eb611eaf3bd6753048d94e0e049c07ad30` | Apache-2.0 |
| Workspace-Bench workspace archives | [`Workspace-Bench/Workspace-Bench-Workspaces`](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Workspaces) | `e245d63bfa20cfdb708cd8e78145ffb087155857` | Apache-2.0 |
| Workspace-Bench Lite task data | [`Workspace-Bench/Workspace-Bench-Lite`](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite) | `60b08b1cc2e8054afbc3ca2160d37876b4f0765c` | Observed card lacks an explicit license; clarify before publication or reproduce the bytes from the Apache-2.0 full release |
| Mem0 baseline | [`mem0ai/mem0`](https://github.com/mem0ai/mem0) | `e6281ab724a958add8298b70de650913aa2680d1` | Apache-2.0 |
| Graphiti baseline | [`getzep/graphiti`](https://github.com/getzep/graphiti) | `2fc108d6e565c4dc8d864c64a7eaa906167f6a28` | Apache-2.0 |

## WP-8.2 handoff

The next work package should implement one narrow benchmark protocol:

```text
load pinned dataset -> emit ordered ingest records -> query selected IDs
-> write raw retrieval/answer records -> run benchmark-native scorer
```

Shared code is justified only for the run manifest, cost preflight, timing, and result envelope.
Benchmark-specific parsing stays in benchmark-specific adapters. No plugin system, workflow
engine, hosted dashboard, or generalized dataset DSL is needed.

Workspace-Bench follows a second thin path because its official boundary is an artifact-producing
agent rather than a reader:

```text
pin code + task data + workspace archives -> inventory and preflight every file
-> ingest complete immutable role workspaces -> run exact task IDs in isolated native and
RememberStack arms -> collect upstream rubrics/dependency scores -> report matched deltas,
format coverage, retrieval diagnostics, latency, and cost
```

The complete rationale and integrity constraints are in
[`workspacebench_benchmark_analysis.md`](workspacebench_benchmark_analysis.md).
