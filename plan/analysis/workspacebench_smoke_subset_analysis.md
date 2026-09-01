# Workspace-Bench smoke and development-subset analysis

**Analysis date:** 2026-09-01

**Assumption:** RememberStack can route and ingest every source format required by the selected
tasks. Format coverage and conversion cost are still reported, but unsupported MIME types are not
used to shrink the sample.

**Status:** proposed task manifests and run protocol; no Workspace-Bench task, agent, judge, or
RememberStack ingestion run has been performed

## Recommendation

Use one immutable ingestion of all five English role workspaces, then execute two nested task
tiers:

- **Smoke:** task IDs `124, 300, 340, 358, 388` in both the native-filesystem control and the
  matched RememberStack arm.
- **Development:** task IDs
  `3, 15, 44, 124, 127, 152, 154, 171, 224, 266, 286, 300, 337, 340, 354, 358,
  363, 374, 380, 388` in both arms. The five smoke tasks are included.

The five-task tier is an operational proof, not a score claim. The 20-task tier is a deliberately
coverage-heavy diagnostic slice, not an unbiased estimator of the official 100-task Lite result.
Only the full pinned Lite manifest produces the publication headline.

This structure keeps the expensive part honest. Subsampling reduces task-agent and judge calls,
but it does **not** reduce workspace ingestion: selecting only each task's known input files would
remove the noisy-workspace discovery problem and leak benchmark structure into the memory build.

## Dataset coordinate

The proposed IDs come from the English metadata table at:

| Coordinate | Value |
|---|---|
| Lite dataset revision | `60b08b1cc2e8054afbc3ca2160d37876b4f0765c` |
| File | `task_lite_clean_en_metadata_table.csv` |
| Downloaded file SHA-256 | `7ef9e4e0e922a465e517a292fc5fb95fc60bd15d61c95ed3ad04ba34f8c42065` |
| Rows | 100 English tasks |
| Smoke ID-list SHA-256 | `8dc4e829092677f2bef3ad52d8be373145dd3a930c7fc55a610c253436a9354e` |
| Development ID-list SHA-256 | `e97ac7e89cda7449554445b66ec01f2bd2d016c938a20519a0b941ea40ecfb44` |

The ID-list hashes use ascending decimal IDs, one per line, with a final newline. Executable
manifests must additionally pin the task metadata bytes, workspace archive digest, upstream runner
commit, adapter version, RememberStack source/configuration, model, harness, prompts, resources,
and judge. An ID list alone is not a protocol identity.

## Selection rules

Selection is fixed before any model or RememberStack result is observed. The selection process may
read these structural fields:

- `absolute_id`, `language`, `persona`, and `task_diff`;
- `tested_capabilities` labels;
- input filenames/extensions from `data_manifest`;
- output filenames/extensions from `output_files`; and
- the number of edges in `file_dep_graph`.

It must not read rubric text, gold values, reference outputs, model traces, upstream per-task
scores, or RememberStack results. The dependency graph may contribute only its edge count during
selection; its source/target identities remain hidden until post-run scoring.

The smoke selection applies these priorities in order:

1. exactly one task per professional role;
2. cover all six official capability labels;
3. include easy, medium, and hard tasks;
4. include low (0–2), moderate (3–5), and high (6+) dependency-edge bands;
5. maximize source and output-format variety without using a very large task as a proxy for the
   full suite; and
6. keep every task independently isolated and scoreable by the official runner.

The development selection then:

1. includes every smoke task;
2. selects exactly four tasks per role, so one role cannot hide a broken route or weak retrieval
   behavior;
3. uses 3 easy, 10 medium, and 7 hard tasks, close to the Lite split's 14/54/32 distribution;
4. covers every source extension appearing in Lite task manifests and every requested output
   extension; and
5. intentionally over-samples heterogeneous and dependency-dense work. This increases diagnostic
   power but is why the aggregate must be labeled a development-slice result.

## Five-task smoke

| ID | Role | Difficulty / edge band | Source extensions | Outputs | Capabilities exercised |
|---:|---|---|---|---|---|
| 124 | Researcher | hard / low | PDF, PNG | TXT | exploration, lineage, semantic relations, heterogeneous files |
| 300 | Backend Developer | hard / high | JSON, legacy PPT/XLS, TXT, XLSX | CSV, JSON, Markdown | exploration, task-providing files, lineage, semantic relations, heterogeneous files |
| 340 | Operations Manager | medium / moderate | DOCX, PPTX, XLSX | CSV | exploration, task-providing files, semantic relations, heterogeneous files |
| 358 | Logistics Manager | easy / moderate | CSV, TXT | CSV | exploration, task-providing files, heterogeneous files |
| 388 | Product Manager | medium / moderate | JSON, TXT, XLSX | PPTX | all six labels, including result-providing files |

Aggregate structural coverage:

| Measure | Smoke value |
|---|---:|
| Roles | 5 of 5 |
| Capability labels | 6 of 6 |
| Difficulties | 1 easy, 2 medium, 2 hard |
| Edge bands | 1 low, 3 moderate, 1 high |
| Task-manifest source occurrences | 22 |
| Distinct task-manifest source extensions | 10: CSV, DOCX, JSON, PDF, PNG, PPT, PPTX, TXT, XLS, XLSX |
| Requested output files | 7 |
| Distinct output extensions | 5: CSV, JSON, Markdown, PPTX, TXT |
| Dependency edges | 21 |
| Rubrics scored after the run | 88 |

Task 300 is intentionally demanding: it covers legacy Office inputs and requests three output
formats. A failure there may be artifact-authoring or task-agent failure rather than memory
failure, which is precisely why both arms and per-stage failure classes are required.

## Twenty-task development slice

| ID | Role | Difficulty | Input extensions | Output extensions | Dependency edges |
|---:|---|---|---|---|---:|
| 3 | Backend Developer | medium | JSON, Markdown, XML | Markdown | 37 |
| 15 | Product Manager | hard | XLSX | XLSX | 2 |
| 44 | Product Manager | medium | XLSX | HTML | 6 |
| 124 | Researcher | hard | PDF, PNG | TXT | 2 |
| 127 | Researcher | medium | Python | TXT | 8 |
| 152 | Researcher | hard | PNG | PNG | 3 |
| 154 | Operations Manager | easy | CSV, Markdown | Markdown | 6 |
| 171 | Product Manager | easy | TXT | XLSX | 10 |
| 224 | Operations Manager | hard | Markdown | XLSX | 8 |
| 266 | Backend Developer | medium | DOCX | DOCX | 1 |
| 286 | Backend Developer | medium | Java | TXT | 0 |
| 300 | Backend Developer | hard | JSON, PPT, TXT, XLS, XLSX | CSV, JSON, Markdown | 6 |
| 337 | Logistics Manager | medium | DOC, XLS, XLSX | CSV | 6 |
| 340 | Operations Manager | medium | DOCX, PPTX, XLSX | CSV | 5 |
| 354 | Logistics Manager | hard | DOCX, XLSX | DOC | 6 |
| 358 | Logistics Manager | easy | CSV, TXT | CSV | 5 |
| 363 | Researcher | medium | PDF | PDF | 4 |
| 374 | Logistics Manager | hard | HTML, JSON, Markdown, TXT | Markdown | 4 |
| 380 | Operations Manager | medium | JSON, Markdown | PPTX | 5 |
| 388 | Product Manager | medium | JSON, TXT, XLSX | PPTX | 3 |

Aggregate structural coverage:

| Measure | Development value |
|---|---:|
| Roles | 4 tasks for each of 5 roles |
| Difficulties | 3 easy, 10 medium, 7 hard |
| Edge bands | 4 low, 7 moderate, 9 high |
| Task-manifest source occurrences | 130 |
| Distinct task-manifest source extensions | all 16 in Lite: CSV, DOC, DOCX, HTML, Java, JSON, Markdown, PDF, PNG, PPT, PPTX, Python, TXT, XLS, XLSX, XML |
| Requested output files | 27 |
| Distinct output extensions | all 11 in Lite: CSV, DOC, DOCX, HTML, JSON, Markdown, PDF, PNG, PPTX, TXT, XLSX |
| Dependency edges | 127 |
| Rubrics scored after the run | 380 |

The slice intentionally contains more high-edge tasks (45%) than Lite as a whole (32%). Its score
should therefore be reported by task, role, difficulty, capability, and edge band, plus an
unweighted slice aggregate. Do not reweight it into a claimed Lite score. If an early unbiased
estimate becomes necessary, create a separate probability sample and pre-register its inclusion
weights; do not repurpose this diagnostic slice after seeing results.

## Run protocol

### 1. Prepare the shared memory build

1. Verify the pinned English workspace archive, task metadata, upstream runner, and license gate.
2. Inventory the entire extracted workspace tree before reading task gold data.
3. Ingest all five complete role workspaces through the ordinary E0 path. Do not ingest only the
   130 files named by the development tasks.
4. Wait for the exact required pipeline stages and projections to reach terminal readiness.
5. Seal one read-only baseline deployment per role. Record its deployment ID, source revision,
   component generations, converter routes, counts, bytes, failures, provider calls/tokens/cost,
   and readiness coordinates.
6. Join task dependency labels only after the build is sealed, for scoring and readiness checks.

Under the stated all-formats-ingestible assumption, every selected dependency source must be
ready. Every workspace file must also have a terminal, accounted outcome; a global conversion
failure or deliberate exclusion remains visible even when it is not a selected gold dependency.

### 2. Execute matched task pairs

For each task ID:

1. Start a fresh official task-isolated container and a fresh agent session.
2. Run the **native control** with the task prompt, native workspace filesystem, and pinned
   artifact-authoring tools.
3. Run the **RememberStack arm** with the identical inputs and tools plus only the shipping public
   read plane and its pinned consumption instructions.
4. Give the memory arm read-only access to the sealed role deployment. Query traces may persist as
   benchmark artifacts, but task outputs and agent conversation state may not enter the next task.
5. Alternate arm order by ascending manifest ordinal: native-first for ordinal 0, memory-first for
   ordinal 1, and so on. This balances provider/time order without using outcomes.
6. Apply the same wall-clock, interaction, output-token, container CPU/RAM/storage, retry, and
   failure-accounting rules to both arms.

The control and memory arms both retain the native filesystem because Workspace-Bench requires
real file inspection and output creation. The memory arm is an addition, not a replacement task.

### 3. Score blindly and completely

1. First run deterministic structural checks: runner completion, expected output filenames,
   artifact readability, isolation receipt, trace parseability, and cost receipt completeness.
2. Strip arm labels from judge-visible paths and metadata. Assign opaque candidate IDs before
   invoking the frozen official judge.
3. Judge both outputs for every task with the same judge model, prompt, resources, and retry rule.
   Deterministically alternate paired judge order separately from agent order.
4. Keep timeouts, missing files, invalid artifacts, parse errors, and judge failures in the
   denominator under explicit failure classes.
5. Preserve raw output files, task traces, rubric decisions, dependency graphs, retrieval traces,
   and cost receipts. Aggregate artifacts must be reproducible from those raw records.

### 4. Report paired results

Report at least:

- official rubric pass rate, task-completion thresholds, and dependency Node/Edge F1 per arm;
- paired per-task rubric delta, wins/ties/losses, and failure-class delta;
- results by role, difficulty, capability, edge band, input-format set, and output format;
- required-file recall and complete-required-file success for memory-returned file paths;
- memory query count, zero-result rate, returned context size, and P50/P95 latency;
- filesystem reads following memory nominations, kept distinct from memory returns;
- total and per-stage ingestion, agent, judge, and provider calls/tokens/currency; and
- total workspace build cost plus amortized build cost per task, never only the amortized value.

Do not gate smoke success on RememberStack beating control. Five paired tasks cannot distinguish a
real quality change from task/model variance. Smoke gates operational integrity; development
provides exploratory quality evidence; Lite-100 provides the publication result.

## Gates and call envelope

### Smoke acceptance

- exact dataset, workspace, runner, model, prompt, tool, resource, and judge pins verified;
- all five role deployments sealed at the required readiness coordinates;
- all 22 selected source-file occurrences ready and attributable;
- 10 isolated task-agent executions completed or retained as classified failures;
- 10 judge evaluations completed or retained as classified failures;
- no rubric, dependency graph, reference output, prior-task state, or evaluator metadata was
  visible to either tested agent;
- the memory arm used only shipping public read operations; and
- one complete machine-readable report accounts for outputs, traces, latency, calls, tokens, and
  currency.

### Development continuation

The development manifest contains the smoke manifest. When every protocol coordinate is unchanged,
reuse the five already-scored task pairs and run only the remaining 15 pairs. Any changed model,
prompt, tool catalog, component generation, workspace bytes, runner, judge, or resource limit
creates a new run key and forbids reuse.

Let `B` be the one-time five-workspace ingestion/build cost, `A` one task-agent execution, `J` one
judge evaluation, and `Q` RememberStack's zero-LLM query cost/latency accounting. Then:

```text
smoke total       = B + 10A + 10J + Q(smoke memory arm)
development total = B + 40A + 40J + Q(development memory arm)
incremental after an unchanged successful smoke
                  = 30A + 30J + Q(remaining memory tasks)
```

The actual preflight expands `A`, `J`, and `B` into calls, input/output/cached tokens, provider
currency, wall time, storage, and failures. It refuses to start before explicit ingest and
whole-run caps are acknowledged.

## What this sample does not prove

- It does not cover all 74 workspace extensions in task-critical paths. The full ingestion audit
  covers those; the development tasks directly require the 16 extensions present in Lite task
  manifests.
- It does not estimate the official Lite score or establish statistical significance.
- It does not isolate converter quality from retrieval, reasoning, and artifact-authoring quality;
  stage metrics and the matched control make those failure sources visible.
- It does not test watched-file mutation, cross-task learning, bi-temporal as-of answers,
  retraction, or hard forget. Those require separate protocol identities or the internal
  capability battery.
