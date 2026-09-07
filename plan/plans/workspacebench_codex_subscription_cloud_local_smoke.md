# Workspace-Bench cloud-memory/local-Codex smoke plan

**Status:** implementation plan for the experimental task-300 smoke

**Baseline:** RememberStack `origin/main` at `8fad369d341950b869dd2f3f8acbce4693b63cea`

**Upstream Workspace-Bench pin:** `3fbd0f1a136720fece86786545983e26642c3db2`

**Related analysis:**

- [`workspacebench_benchmark_analysis.md`](../analysis/workspacebench_benchmark_analysis.md)
- [`workspacebench_smoke_subset_analysis.md`](../analysis/workspacebench_smoke_subset_analysis.md)
- [`locomo_codex_subscription_bridge.md`](../analysis/locomo_codex_subscription_bridge.md)

## Objective

Implement a reproducible experimental Workspace-Bench path in which:

1. a complete, pinned role workspace is processed once by a cloud RememberStack deployment;
2. the Workspace-Bench task agent runs on localhost against a pristine local copy of the same
   native workspace;
3. Codex uses the operator's existing ChatGPT subscription through the official Codex runtime,
   not an OpenAI API key or a private ChatGPT endpoint;
4. the memory arm gets the shipping Remember MCP read plane through a narrow local tunnel while
   the native arm does not;
5. both arms produce artifacts locally and are judged locally after the task agent exits; and
6. task 300 is the first live smoke because it covers `.ppt`, `.xls`, `.xlsx`, `.json`, and `.txt`
   inputs and `.md`, `.csv`, and `.json` outputs.

"Local" describes orchestration, files, tools, traces, and output custody. Codex inference still
runs on OpenAI's service. The frozen official Workspace-Bench judge also calls its configured
Anthropic-compatible inference endpoint even when the judge process and judge view run locally.

## Binding boundaries

### Preserve the benchmark treatment

Both arms keep the complete native role filesystem and the same task prompt, Codex model,
reasoning effort, office skills, resource limits, timeout, and judge. The sole treatment is that
the memory arm additionally receives the Remember MCP read tools and their fixed consumption
instructions. RememberStack is an augmentation, not a replacement filesystem.

Do not ingest or expose rubrics, rubric types, gold dependency graphs, reference outputs, judge
artifacts, prior-task state, or evaluator-only metadata. Use the upstream isolated task view that
postdates the metadata-leakage fix. The first implementation is a separately fingerprinted
experimental protocol and must not be described as an upstream leaderboard result.

### Reuse the LoCoMo authentication boundary, not its tool policy

Reuse these properties of `CodexSubscriptionModelProvider`:

- the official `openai-codex` app-server/CLI owns ChatGPT authentication and refresh;
- preflight requires an authenticated ChatGPT account rather than accepting an API-key login;
- no code reads, parses, copies, logs, or archives `~/.codex/auth.json`;
- each task starts a fresh ephemeral Codex thread/session; and
- tokens, elapsed time, status, and returned runtime items are recorded without claiming that
  `cost_usd=0` means the subscription seat is free.

Do not reuse LoCoMo's empty read-only sandbox or its prohibition on all runtime actions.
Workspace-Bench is an artifact-producing agent benchmark. Its Codex session needs
`workspace-write`, deny-all/non-interactive approvals, local commands, office tools, file edits,
and—in the memory arm only—the Remember MCP server. Command network access remains disabled.

The supported Codex controls are documented in:

- <https://developers.openai.com/codex/auth/> (`codex login`, ChatGPT subscription access, and
  credential-storage warnings);
- <https://learn.chatgpt.com/codex/sandboxing> (`workspace-write` and approval boundaries); and
- <https://developers.openai.com/codex/mcp/> (stdio MCP configuration and `enabled_tools`).

### Never put subscription or Remember credentials in task material

Credentials must not appear in prompts, workspaces, generated Codex config files, command-line
arguments, traces, result envelopes, or judge views. The runner may ask the official Codex runtime
to inspect the active account, but it must not inspect the credential cache itself. Remember MCP
uses an operator-owned ambient Remember credential outside the task workspace, preferably a
query-only deployment token, and a localhost SSH tunnel or narrow HTTPS endpoint.

Before a live subscription run, execute a disposable credential-isolation canary using a fake
secret. If a model-generated command can read the credential location, or if the fake value enters
the Codex trace/result tree, the live run must fail closed. Do not weaken this gate merely to make
Docker convenient. Official OpenAI documentation permits copying an auth cache into a trusted
container, but this protocol deliberately keeps the stricter existing LoCoMo rule: no auth-cache
copying by the benchmark adapter.

## Architecture

```text
complete pinned Backend Developer workspace
        |
        +--> cloud RememberStack ingest --> readiness receipt --> sealed deployment
        |                                                   |
        |                                                   +--> localhost tunnel
        |                                                          |
        +--> pristine local task workspace                         v
                    |                                      remember mcp --read-only
                    |                                              |
                    +--> native Codex arm              memory Codex arm
                              |                                  |
                              +---------- local artifacts --------+
                                                   |
                                      blind local judge views
                                                   |
                                  official score + paired report
```

The cloud and local workspace inventories must have the same content-root digest. The deployment
receipt binds the workspace digest, RememberStack revision, converter/router configuration,
component generations, version IDs, readiness requirements, API origin, and deployment identity.
The local run refuses a receipt whose digest or required readiness coordinates do not match.

## Implementation packages

### WP1 — Fail-closed read-only remote MCP mode

Build on the current `remember` facade in `src/remember/`, not the compatibility re-exports in
`src/rememberstack/surfaces/`.

Add `remember mcp --read-only` while preserving the current default behavior:

- default `remember mcp` continues to list memory-write/readiness tools, assured operations, and
  open-query tools in its current stable order;
- `--read-only` omits all `MEMORY_WRITE_TOOL_NAMES` descriptors, including ingest and pipeline
  readiness, while retaining dynamically discovered assured operations and composed open-query
  tools;
- a direct `tools/call` for an omitted write tool fails explicitly and never falls through to an
  assured operation with the same name;
- the mode is represented as a typed constructor/configuration value rather than an ambient
  environment branch; and
- stdio protocol behavior and backwards-compatible import surfaces remain unchanged.

The Workspace-Bench runner must additionally configure Codex's `enabled_tools` with the exact
read-tool names observed during preflight. This double boundary makes the treatment auditable and
protects against a later MCP catalog expansion.

### WP2 — Workspace-Bench adapter and immutable receipts

Add a self-contained `benchmarks/workspacebench/` package and tests under
`src/tests/benchmarks/`. Do not vendor Workspace-Bench code or datasets.

The adapter must provide a CLI through `python -m benchmarks.workspacebench` with these initial
commands:

- `preflight`: verify the external upstream checkout commit, required post-leakage runner files,
  exact task ID, task metadata/data-manifest shape, local workspace tree digest, Codex ChatGPT
  account, Remember API/MCP discovery for the memory arm, and cloud deployment receipt;
- `run-agent`: run one requested arm against an already prepared isolated task workspace and write
  a durable result envelope plus raw/sanitized Codex trace; and
- `run-pair`: preflight once, clone the same pristine task workspace into two isolated local case
  directories, alternate or explicitly pin arm order, invoke both arms, and emit a paired receipt.

The first live CLI supports exactly task 300 by default, but models and code must not hard-code
its filenames. Additional task IDs can be accepted only when explicitly supplied and must remain
separately fingerprinted. External paths must be explicit and absolute. Nothing auto-downloads an
18.7 GB archive or starts paid ingestion as a side effect of `preflight` or `run-agent`.

Use typed Pydantic models for at least:

- protocol coordinates and upstream/data/workspace hashes;
- cloud deployment/readiness receipt;
- arm configuration and exact MCP tool allowlist;
- account attestation without secret material;
- task result, output manifest, trace manifest, token usage, timing, and classified failure; and
- paired-run manifest binding the two pristine workspace digests.

Every JSON artifact has a schema version and is written atomically. Never store absolute secret
locations or credentials. Hash files as streamed bytes, reject symlinks escaping the allowed
root, and keep generated/result directories outside the source workspace digest.

### WP3 — Task-scoped Codex subscription runner

Implement a Workspace-Bench-specific runner rather than widening the generic LoCoMo provider.
Prefer the already pinned `openai-codex==0.147.0` app-server SDK so account attestation and runtime
items share the existing implementation vocabulary.

Required behavior:

- require account type `chatgpt`; fail before task execution otherwise;
- one fresh ephemeral thread/session per task arm;
- pin the model, explicit reasoning effort, temperature semantics supported by Codex, and client
  identity in the protocol receipt;
- use the prepared local task workspace as `cwd`, `Sandbox.workspace_write`, and deny-all approval
  requests so the unattended run cannot expand its authority;
- leave command network access disabled; only the runner-owned Remember stdio MCP may reach the
  configured local tunnel;
- native arm exposes no Remember MCP configuration;
- memory arm registers `remember mcp --read-only` through per-process Codex config overrides,
  marks it required, and sets both `enabled_tools` and bounded startup/tool timeouts;
- inject a fixed, versioned memory-consumption instruction only in the memory arm; preserve the
  upstream task prompt verbatim otherwise;
- allow ordinary command executions, workspace-local file changes, and allowlisted Remember MCP
  calls; classify web searches, subagents, unknown MCP servers/tools, writes outside the task
  workspace, approval requests, or network escalation as protocol violations;
- record command/file/MCP event metadata and MCP arguments/results needed for retrieval diagnostics
  while applying the existing secret redaction rules;
- collect only expected output files rooted below the official output directory and produce their
  paths, sizes, MIME types, and SHA-256 hashes; and
- never upload task outputs or conversation state into RememberStack.

The synchronous SDK does not provide a reliable benchmark wall-clock deadline. Run each task
session behind an external process-group supervisor: terminate, wait a short grace period, then
kill the complete process group. Preserve partial stdout/stderr/runtime events and classify the
timeout. The task runner itself must never retry an entire agent task automatically; a retry is a
new attempt with a new ID and receipt.

If the SDK cannot expose the exact toolful behavior or trace fidelity required by the upstream
Codex harness, use the stock Codex CLI as the execution backend but retain SDK account preflight.
Document and fingerprint that decision. Do not silently fall back between SDK and CLI.

### WP4 — Cloud handoff and current MCP preflight

The initial code consumes a cloud receipt rather than implementing or triggering the expensive
role-workspace ingest. Provide a documented receipt-generation handoff for the future cloud job:

1. inventory and hash the complete role workspace before ingest;
2. ingest through ordinary E0 using the pinned conversion/router configuration;
3. wait for the declared version IDs and required readiness coordinates;
4. seal the deployment read-only for task execution;
5. write a signed or operator-attested receipt containing no bearer token; and
6. establish an SSH local forward or HTTPS endpoint before local preflight.

Local preflight must use the refreshed MCP composition rules:

- call `GET /operations` for assured operations;
- accept open-query tools only when `GET /query/space` provides the authoritative
  `memory_v1`/major-1 identity and valid surface-manifest hash;
- start `remember mcp --read-only`, complete MCP initialize and `tools/list`, and bind the returned
  names/descriptors into the run manifest; and
- reject missing required tools, write tools, discovery drift, auth errors, transport errors, or a
  receipt origin that does not match the tunnel/endpoint.

### WP5 — Upstream execution and local judge handoff

Keep the adapter thin. Reuse the pinned upstream task-view preparation, metadata hiding,
per-task isolation, office skill pack, output collection, judge-view preparation, and official
scorer. Any overlay or compatibility shim applied to the external checkout must:

- be stored and hashed in this repository;
- apply only to a clean checkout at the exact upstream commit;
- fail on target-file hash drift instead of fuzzy-applying;
- never modify the source checkout in place—materialize a disposable run copy/worktree; and
- be included in the protocol fingerprint.

The official judge remains the upstream ClaudeCode/Anthropic-compatible judge, launched from
localhost only after the agent finishes and evaluation metadata is restored into the hidden
evaluator view. A future Codex-subscription judge is a separate experimental protocol and is not
part of this work package.

If a full Docker-integrated subscription run cannot satisfy the credential-isolation canary,
deliver and document the host-local task runner as the smoke path and mark container equivalence
as an unsatisfied publication gate. Do not mount or copy the operator's Codex auth cache merely to
claim upstream container parity.

### WP6 — Tests, documentation, and dry smoke

Unit and integration tests must not invoke paid model calls, download datasets, use real bearer
tokens, or require a live RememberStack deployment. Inject account, Codex turn, process, MCP, and
filesystem seams.

Minimum acceptance tests:

- existing `remember mcp` default tool-list/call behavior is unchanged;
- `--read-only` hides and rejects every write/readiness tool while retaining assured/open-query
  tools and failing closed on remote discovery errors;
- preflight accepts only the pinned upstream revision and rejects dirty/wrong revisions;
- task 300 metadata is sanitized before the tested agent can see it;
- native and memory arms start from byte-identical pristine workspace digests;
- only the memory arm receives the exact read-only MCP allowlist and versioned instruction;
- ChatGPT account attestation rejects API-key/missing accounts without exposing account secrets;
- task outputs cannot escape the allowed output root through `..` or symlinks;
- timeouts kill the complete fake process group and retain a classified receipt;
- fake credentials cannot appear in serialized configs, traces, logs, or result trees;
- disallowed runtime actions produce a protocol-violation result rather than a scoreable success;
- partial/missing/invalid artifacts remain explicit failures; and
- report reconstruction from raw receipts is deterministic.

Document operator steps in `benchmarks/workspacebench/README.md`, clearly separating:

1. external dataset/workspace preparation;
2. cloud ingest and receipt production;
3. local `codex login` and `codex login status`;
4. tunnel setup and ambient Remember login;
5. no-spend `preflight`;
6. native and memory agent execution;
7. local official judge invocation; and
8. report generation.

Include copy-paste examples for task 300, but label commands that are scaffolding versus commands
that are actually implemented and tested. The dry smoke uses a tiny synthetic workspace with the
same input/output extension classes; the real task-300 run remains operator-authorized because it
uses subscription capacity and an external processed corpus.

## Review checkpoints

The reviewer should stop the implementation at any checkpoint that weakens the benchmark or
credential boundary:

1. **MCP checkpoint:** read-only mode is based on current `remember` MCP composition and cannot
   call hidden write tools.
2. **Authentication checkpoint:** no code reads/copies `auth.json`, and the fake-secret canary is
   green.
3. **Isolation checkpoint:** both arms receive identical pristine local workspace bytes; evaluator
   metadata stays hidden.
4. **Treatment checkpoint:** the only arm difference is the versioned Remember instruction and
   exact MCP tool allowlist.
5. **Trace checkpoint:** outputs, actions, MCP calls, usage, timings, failures, and protocol pins
   are sufficient to reconstruct a paired result.
6. **Upstream checkpoint:** all upstream modifications are explicit, cleanly pinned overlays and
   the judge remains the official frozen judge.

## First live acceptance

A first live smoke is complete only when task 300 has:

- one sealed cloud Backend Developer deployment receipt matching the local full-workspace digest;
- a green no-spend preflight, including MCP discovery and credential-isolation canary;
- one native and one memory Codex subscription task execution under identical limits;
- expected `.md`, `.csv`, and `.json` outputs or explicit classified failures for each arm;
- one blind official judge result for each arm;
- full raw/sanitized traces and subscription token counts;
- memory query count, latency, zero-result rate, returned context size, and attributable file paths;
  and
- a paired report that labels the protocol experimental and does not infer benchmark quality from
  a single task.

This plan does not authorize the paid cloud ingest, the live task-agent calls, or the judge calls.
Implementation and synthetic tests may proceed without those external side effects.
