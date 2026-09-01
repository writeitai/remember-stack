# Workspace-Bench heterogeneous-workspace benchmark analysis

**Research date:** 2026-09-01

**Status:** selected external benchmark track; protocol design required before adapter work; no
dataset bytes have been added to this repository and no benchmark run has been performed

## Recommendation

Add [Workspace-Bench](https://github.com/OpenDataBox/Workspace-Bench) beside LoCoMo in the
external benchmark portfolio. The two answer different questions:

- **LoCoMo** measures long-running conversational memory over timestamped text.
- **Workspace-Bench** measures whether an agent can find, relate, and use information spread across
  a large, noisy workspace containing many file formats and historical versions.

Workspace-Bench is especially relevant to RememberStack because its corpus contains 20,476 files
across 74 file extensions. Documents and spreadsheets dominate, but the benchmark also includes
PDF, Markdown, presentations, source code, configuration, email, images, archives, and statistical
data formats. It therefore provides an external reason to measure both **format coverage** and the
**cost of turning heterogeneous bytes into searchable memory**.

It is not, however, a drop-in memory-backend benchmark. Its official result measures the whole
task agent: workspace exploration, file reading and editing, reasoning, output-artifact creation,
and an agent-as-a-judge rubric score. RememberStack must therefore publish two clearly separated
results:

1. a direct, deterministic **ingestion and retrieval audit** over the pinned workspace bytes; and
2. an official **agent + memory** result from a matched pair: the same task agent with the native
   filesystem alone, and with the native filesystem plus RememberStack's ordinary public read
   plane.

The matched pair measures the incremental value of memory without pretending that artifact
generation quality is a memory-only metric.

As of this snapshot, Workspace-Bench is a new arXiv preprint with an actively changing public
runner, not an established memory-system standard. Treat it as external capability and economics
evidence, not as the cross-vendor memory headline. The
[project site](https://workspace-bench.github.io/) and every published result must link the exact
code, task-data, workspace-data, and judge coordinates used.

## What the benchmark contributes

The [paper](https://arxiv.org/pdf/2605.03596) and current official repository describe:

| Property | Published shape | Why it matters here |
|---|---:|---|
| Professional workspaces | 5 | Operations, logistics, product, research, and backend-development corpora exercise different format mixes. |
| Files | 20,476 | Broad-workspace discovery prevents a gold-file-only ingestion shortcut. |
| File extensions | 74 | Exercises converter routing and unsupported-format honesty, not only text retrieval. |
| Tasks | 388 full; 100 Lite | Lite is the bounded publication surface; the authors report about 70% lower evaluation cost than full. |
| Rubrics | 7,399 full; 19.1 per task on average | Fine-grained output scoring is useful, but requires the official judge and remains an agent-level measure. |
| Dependency annotations | 4.7 essential files and 5.1 edges per task on average | Enables file-discovery and file-relation diagnostics in addition to final-output judging. |
| Workspace scale | as large as 11,020 files and 20 GB | Makes ingestion throughput, deduplication, storage, and build cost first-class benchmark outputs. |
| File history | draft/revision/final variants in part of the corpus | Exercises lineage selection, although filename conventions are not the same as RememberStack's bi-temporal truth contract. |

The 74 extensions are breadth, not 74 independently quality-labeled converter tests. Official
task success provides downstream evidence that important conversions were usable, while the
direct audit reports mechanical coverage and cost. It does not by itself prove accurate OCR,
formula recovery, slide layout understanding, or media transcription for every format.

## Binding comparison boundary

### Two matched task arms

Every scored task runs twice under the upstream task-isolated container protocol:

| Arm | Agent inputs and tools | Purpose |
|---|---|---|
| Native control | Pinned task prompt, the official native workspace filesystem, and the fixed artifact-authoring tools | Establish what the chosen model and harness can do without RememberStack. |
| RememberStack | Everything in the native control, plus the exact shipping API/MCP read operations and consumption instructions | Measure the incremental effect of indexed memory without removing the filesystem needed to create outputs. |

The arms pin the same task IDs, upstream code and data revisions, model, harness, system prompt,
tool budget, wall-clock and container resources, output-token limit, and judge. The memory arm may
use only shipping public operations; it gets no benchmark-specific SQL, hidden dependency lookup,
or gold-file search endpoint. Upstream or vendor leaderboard numbers are contextual unless every
one of these coordinates matches.

The native filesystem stays available in both arms. Workspace-Bench asks agents to inspect and
write real files, so replacing it with a memory API would change the task instead of evaluating
memory.

### Clean information boundary

Only the workspace bytes available to the tested agent may enter RememberStack. The following
remain outside ingestion and outside answer context:

- task rubrics and rubric types;
- the gold dependency graph;
- reference outputs or expected values; and
- evaluator and judge artifacts.

The pinned upstream commit must include the fix for the disclosed metadata-leakage issue. A run
preflight verifies that task containers cannot read `metadata.json` or equivalent evaluator state
outside their allowed workspace. Any leak invalidates the whole run rather than only affected
tasks.

### Workspace isolation and reuse

Index each immutable role workspace once per exact run key and amortize that build across its
selected tasks. The key includes the upstream workspace archive hash, language, RememberStack
source revision, converter/router configuration, all component generations, and benchmark-adapter
version.

Task-generated files must not contaminate another task. The initial comparison gives RememberStack
read-only access to a baseline workspace deployment. If a later protocol tests watched-file
updates, each task receives an independently restored deployment and that result gets a distinct
protocol identity. It is never mixed with the read-only baseline.

## Ingestion and retrieval audit

Before spending on task agents or judges, the adapter inventories the exact workspace tree and
produces one row per extension and resolved MIME type with:

- file count and raw bytes;
- unique content hashes, duplicate bytes avoided, and archive/container status;
- configured converter route and whether it is local/deterministic or provider-backed;
- succeeded, unsupported, skipped-by-declared-policy, failed, and timed-out counts;
- converted characters/blocks and derived assets;
- converter, OCR, ASR, VLM, embedding, extraction, and checker calls, tokens, currency, and wall
  time; and
- coverage weighted three ways: all files, all bytes, and files that are gold dependency nodes.

Gold dependency membership is joined only after ingestion finishes. It may score coverage but may
not choose which files are ingested, routed, retried, or indexed. Unsupported and intentionally
skipped files remain in the denominator; a missing route is never reported as a successful empty
document.

The task trace adds retrieval diagnostics where paths can be attributed:

- required-file precision and recall over distinct files returned by RememberStack;
- complete-required-file success per task;
- dependency-edge discovery from the official scorer;
- query count, P50/P95 latency, returned context bytes/tokens, and zero-result rate; and
- filesystem reads after a memory result, so a memory nomination is not confused with verified
  source use.

These are supporting diagnostics. Official rubric pass rate, task-completion thresholds, and
dependency Node/Edge F1 remain the headline Workspace-Bench metrics.

## Cost-control contract

Workspace-Bench-Lite reduces task count, not the cost of indexing broad workspaces. The paper's
evaluated agents consume hundreds of thousands of model tokens per task in many configurations,
and the official judge performs another agent run over outputs and source files. A paired 100-task
result is therefore a deliberate publication event, not a routine regression test.

The adapter must keep that spend bounded:

1. **Download and hash once.** Do not vendor the dataset. Cache the pinned English archive by
   digest and verify it before reuse.
2. **Inventory before conversion.** Resolve every extension/MIME route and report projected local
   work, provider calls, tokens, and currency before processing bytes.
3. **Stop before partial paid ingest.** Require explicit ingest and whole-run ceilings. A route
   gap or projected cap violation fails during preflight.
4. **Exploit content identity, never gold labels.** Exact-byte deduplication and immutable
   representation reuse are valid cost savings; selecting only dependency files is benchmark
   leakage.
5. **Separate cheap and paid stages.** Report raw hashing, deterministic conversion, provider
   conversion, embedding/extraction, task-agent, and judge costs independently. A cheap local
   route must not hide later extraction spend.
6. **Run retrieval diagnostics first.** Validate ingestion coverage and memory-tool traces before
   authorizing matched answer-agent and judge runs.
7. **Count every failure.** Conversion failures, corrupt files, timeouts, missing outputs, judge
   failures, and unsupported formats stay in their applicable denominators.
8. **Amortize transparently.** Publish total workspace build cost and the amortized cost per
   selected task; do not report only the smaller amortized number.

## Execution tiers

The proposed exact task IDs, selection constraints, paired execution order, gates, and call
envelope are in
[`workspacebench_smoke_subset_analysis.md`](workspacebench_smoke_subset_analysis.md). They were
derived from the pinned official Lite metadata before any RememberStack score was observed.

| Tier | Workspace-Bench protocol | Purpose |
|---|---|---|
| Adapter smoke | IDs `124, 300, 340, 358, 388`, one per role, spanning all capability labels, all difficulty/edge bands, and 10 task-critical source extensions | Validate archive layout, MIME routing, isolation, trace attribution, output collection, and failure accounting. This is not a headline score. |
| Development | 20 fixed Lite tasks with four per role, 3/10/7 easy/medium/hard, all 16 Lite task-manifest source extensions, and all 11 requested output extensions; matched native and RememberStack arms | Iterate with bounded agent/judge spend while retaining all five full role workspaces. Results are labeled as a diagnostic subset. |
| Publication | All 100 official Workspace-Bench-Lite tasks in both matched arms | Primary heterogeneous-workspace result. The 388-task full suite is a separately authorized extended publication run, not the default publication tier. |

## Source and integrity snapshot

Observed on 2026-09-01:

| Artifact | Pinned revision | License/integrity note |
|---|---|---|
| [Evaluation code](https://github.com/OpenDataBox/Workspace-Bench) | `3fbd0f1a136720fece86786545983e26642c3db2` | Repository `LICENSE` contains the MIT terms, although its copyright line names WOLF-Bench. This revision follows the upstream 2026-08-17 evaluator-metadata leakage fix; pinning an earlier vulnerable runner is prohibited. |
| [Full task data](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench) | `3491f9eb611eaf3bd6753048d94e0e049c07ad30` | Dataset card declares Apache-2.0. |
| [Workspace archives](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Workspaces) | `e245d63bfa20cfdb708cd8e78145ffb087155857` | Dataset card declares Apache-2.0. The English zip is about 18.7 GB at this revision and must be content-hash pinned by the adapter. |
| [Lite task data](https://huggingface.co/datasets/Workspace-Bench/Workspace-Bench-Lite) | `60b08b1cc2e8054afbc3ca2160d37876b4f0765c` | The observed Lite card does not declare a license even though the full task and workspace cards do. Clarify this before publishing a Lite result, or derive the committed task subset from the Apache-2.0 full release if that reproduces the official bytes exactly. |

The upstream project is actively changing task files, rubrics, language metadata, harnesses, and
integrity controls. Code, task data, workspace archives, prompts, task patches, and judge must all
be pinned independently. “Workspace-Bench-Lite” without those coordinates is not a reproducible
protocol name.

## Non-goals and limitations

- Workspace-Bench does not replace LoCoMo, LongMemEval, fact-consolidation, or retrieval-only
  benchmarks. It adds format and workspace breadth.
- Its version-like filenames do not prove world-time/system-time retrieval, contradiction
  preservation, retraction, or hard forget. The internal capability battery remains necessary.
- The official rubric judge is not deterministic. Use one frozen judge, preserve raw judgments,
  audit a fixed sample, and report disagreement rather than multiplying full runs.
- A higher agent score cannot be attributed solely to memory. Report the matched delta and direct
  ingestion/retrieval diagnostics together.
- No benchmark result authorizes benchmark-specific behavior in conversion, extraction, or
  retrieval. All routes and public operations must be useful outside Workspace-Bench.

## Adapter handoff

Implementation should remain a thin upstream adapter:

```text
pin and verify upstream artifacts
-> inventory every workspace file and preflight conversion/spend
-> ingest complete immutable role workspaces through ordinary E0
-> run exact task IDs in isolated native and RememberStack arms
-> collect official outputs, traces, rubric judgments, and dependency scores
-> emit coverage/cost/latency metrics and a matched comparison artifact
```

Shared benchmark machinery is limited to immutable run manifests, cost preflight, timing, and
result envelopes already justified by the Phase 8 portfolio. Workspace-Bench-specific archive,
task, trace, and scorer parsing stays in its own adapter.
