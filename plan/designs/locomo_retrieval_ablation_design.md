# LoCoMo retrieval-access ablation design

**Status:** binding when merged.
**Date:** 2026-09-14.
**Decision:** D124.
**Analysis:**
[`locomo_retrieval_access_ablations.md`](../analysis/locomo_retrieval_access_ablations.md).

## 1. Problem and scope

The canonical LoCoMo answer loop exposes a benchmark-composed 22-tool catalog.
It does not tell us how a normal MCP surface compares with direct access to the
P3 corpus. P3 is the published, navigable corpus snapshot: an ordinary directory
tree whose `.snapshot-version` identifies the immutable publication.

This design adds an **answer-and-judge-only development experiment** over one
already-processed LoCoMo sample. It does not change the canonical Full-v31
protocol, ingestion, retrieval contracts, P3 format, public judge rubric or
product claims. It introduces no UMC or RememberFS dependency.

## 2. Decision

One source run may produce four independent ablation outputs:

| Profile key | Runtime | Allowed corpus access |
| --- | --- | --- |
| `codex-p3` | native Codex subscription agent | local P3 filesystem only |
| `codex-p3-mcp` | native Codex subscription agent | local P3 filesystem and OSS RememberStack MCP |
| `mcp` | OpenRouter Luna provider-neutral agent | OSS RememberStack MCP only |
| `mcp-p3` | OpenRouter Luna provider-neutral agent | OSS RememberStack MCP and benchmark P3 MCP tools |

The command is:

```text
python -m benchmarks.locomo retrieval-ablation \
  --run SOURCE_RUN --sample SAMPLE --profile PROFILE \
  --output ABLATION_DIR [--p3-root LOCAL_P3] \
  --max-questions N --max-agent-calls N --max-judge-calls N \
  --max-evaluator-cost-usd USD --execute
```

`--p3-root` is required only by profiles whose key contains `p3`. It is an
ordinary local directory obtained through the existing OSS publication,
restore or copy workflow. Its `.snapshot-version` must equal the P3 version in
the source run's checkpointed readiness.

Every profile answers every prepared item belonging to `--sample`.
`--max-questions` is a run-absolute authorization ceiling, not a first-N
selector; it must cover that exact set.

### 2.1 Valid comparisons

The two causal access comparisons are:

1. `codex-p3` versus `codex-p3-mcp`: what changes when the OSS MCP read surface
   is added to native Codex that already has local P3; and
2. `mcp` versus `mcp-p3`: what changes when the bounded P3 list/search/read
   tools are added to the provider-neutral MCP agent.

Cross-family scores are directional only. Native Codex has its own internal
agent loop and general shell search; the provider-neutral runtime uses the
benchmark step loop and bounded P3 tools. Results must not present all four as
one controlled ranking. Each summary reports provider, model, reasoning effort,
model-call count, runtime-action/tool-call count, and shell-versus-MCP
utilization. The OSS MCP catalog also intentionally excludes the canonical
benchmark's seven direct SDK primitives.

## 3. Source and experiment identity

Before spending, the runner loads and validates the source run's immutable
files and requires:

- the selected sample is completely ingested in one deployment;
- the source run has checkpointed exact Full-v31 readiness;
- for an MCP profile, live documents still equal the source run's ingest
  records and the serving revision/query-space manifest still match that
  source run;
- P3 identity matches readiness for a P3 profile; and
- the ablation output is either empty or already has the identical immutable
  ablation configuration.

`codex-p3` is deliberately offline after source-file and P3-marker validation;
it does not require a live database merely to read an already-published tree.

The ablation configuration binds its schema version, source protocol
fingerprint, source processing revision, ablation-runner revision, sample,
selected item IDs, profile, answer/judge provider and model settings, answer and
judge prompt/schema hashes, the MCP catalog hash when present, and the P3
version when present. The runner revision may differ from the source revision;
both are recorded rather than conflated. Absolute local paths are operational
inputs, not experiment identity.

The command writes only under `--output`. It never edits the source run's
`run.json`, `state.json`, answers or judges. A profile has its own checkpointed
answers, judges, provider usage and summary. Rerunning the same command resumes
missing items; a changed identity fails closed and requires a new directory.

## 4. MCP profiles

The OSS `RemoteOperationMcpServer` remains the authority for RememberStack
tools. It gains an explicit read-only composition used by both experimental
paths: `list_tools()` omits ingest/readiness and `call_tool()` refuses them
before any API action. The provider-neutral loop calls those Python methods,
preserving the model-visible MCP shapes while avoiding semantically irrelevant
STDIO framing.

Every advertised name must be one assured or open-query read tool; there are no
benchmark direct-SDK primitives. Definitions supplied to the model contain only
MCP `name`, `description` and `inputSchema`; a product result schema is never
copied into the prompt.

`mcp-p3` appends `p3_list`, `p3_search` and `p3_read` through a benchmark-local
MCP composite. They preserve the existing `P3Mount` validation, semantics and
operative bounds and return the normal MCP `{content, isError}` result. The
plain `mcp` profile cannot see those tools or the filesystem. These three tools
are intentionally not shell parity: they measure a portable, bounded P3 tool
surface, while native Codex measures general filesystem use.

The provider-neutral answer loop retains Full-v31's eight-tool/nine-model-call
ceilings, retry behavior, duplicate-call rule, content-before-`Unknown` guard
and complete tool trace. Its small MCP host validates `{content, isError}` and
decodes the sole JSON text block before giving it to the existing trace and
guard: `isError` maps to a failed call, row-returning SQL remains
content-bearing, and a typed `QueryResult/v1` error remains correctable when
Full-v31 treats its code as correctable. Remote API failures retain their
status and code inside the error text, so a 503 stops and checkpoints the run
instead of consuming the question's retry budget. The stored trace is the
decoded public JSON the model used, rather than redundant JSON-RPC framing.
This experiment does not introduce response compaction.

## 5. Native Codex profiles

Each question runs in one fresh ephemeral Codex thread with `gpt-5.6-luna`,
reasoning effort `high`, ChatGPT-subscription authentication,
`ApprovalMode.deny_all`, `Sandbox.full_access`, and this strict output shape:

```text
{"answer": "shortest complete final answer"}
```

A fresh working directory, disjoint from the source run and dataset, contains
only a `corpus/` link to the P3 root:

```text
$TEMP/question/
└── corpus/ -> validated P3 snapshot
```

The route instruction says:

- search and read only within `corpus/`;
- do not modify any file;
- do not visit parent directories;
- do not use the Internet; and
- do not seek benchmark gold answers, reference evidence or evaluator files.

This is an experiment-validity instruction, not a security boundary. Codex is
not placed in a hard filesystem/network sandbox and shell output is not given a
benchmark-specific cap, per owner direction. Corpus and MCP text is untrusted
input. An instruction inside it to leave the corpus, change files, call another
tool or use the Internet remains an invalid action; this experiment adds no new
product content filter.

`codex-p3` configures no experiment MCP server. `codex-p3-mcp` adds one required
read-only STDIO server launched from the installed OSS package with
`sys.executable -m remember mcp --read-only`. `CodexConfig.config_overrides`
sets `mcp_servers.rememberstack_locomo.command`, `args`, `required=true`,
`enabled_tools` equal to the read catalog, and `env_vars` containing only the
supported deployment URL/authorization variable names. The subprocess inherits
values from its parent; no token is written to an argument, prompt, output or
audit record. The hybrid route text names both `corpus/` and the MCP tools and
explains their different authority.

Every completed app-server item is inspected. Command requests and
RememberStack MCP requests are allowed and recorded without their returned
content. A web search, file change, subagent, dynamic tool, MCP call to another
server, or any other out-of-profile runtime action makes that question a
visible invalid-result failure. Shell commands that Codex classifies only as
`unknown` remain auditable rather than being mistaken for proof of a write:
ordinary pipelines can receive that coarse label. Command text, parsed command
actions, working directory, exit status, MCP server/tool/arguments/status,
token counts and latency remain in a JSONL audit so a reviewer can detect
instruction-only violations such as writes or parent traversal. The number of
command plus MCP actions may not exceed Full-v31's eight-tool ceiling. Before
any final answer, the trace must show a completed search/read command or a
content-bearing MCP call; a no-match POSIX search (exit 1) counts as an attempt,
while directory lists and identity/schema-only MCP calls do not qualify. A
completed shell pipeline classified only as `unknown` counts provisionally
because that coarse label can hide an ordinary search; the manual audit must
confirm that it actually read the corpus and did not write or escape.

Native Codex performs its own multi-step tool loop inside one model turn, so
`agent_call_count` is one while runtime action count is reported separately.
This is an access-pattern comparison, not a claim that those counts have the
same billing meaning as the provider-neutral loop. The hybrid arm is an
availability treatment: a valid answer need not call MCP when the agent finds
P3 sufficient, but per-question and summary MCP utilization makes that choice
visible.

## 6. Shared answer and judge semantics

All profiles share the current Full-v31 rules for:

- shortest complete final answers;
- claims versus adjudicated facts and their time labels;
- general knowledge as interpretation rather than conversation authority;
- named-entity resolution alongside content retrieval when MCP is present;
- complete enumeration of distinct supported values;
- counterfactual reasoning from retrieved causal evidence;
- no benchmark gold/evaluator access; and
- `Unknown` only after a content-bearing access attempt.

One shared rule block supplies those semantics. Four short access-path
preambles mention only the routes each profile can actually use. Native Codex
returns the final answer object above; the provider-neutral loop returns the
existing tool-or-answer step. Prompt and schema hashes bind both forms.

Every answer arm uses the Luna model family at reasoning effort `high`: native
Codex uses subscription model `gpt-5.6-luna`, and provider-neutral arms use
OpenRouter `openai/gpt-5.6-luna`. The provider/runtime difference remains a
disclosed crossed factor. All profiles use the canonical Full-v31 OpenRouter
Luna judge at its pinned `none` reasoning effort, temperature, prompt, schema,
repetitions and gold answer. Source-protocol provider variants do not silently
add Gemma or Codex seats. Judge output is never visible to a later answer.

## 7. Results, failure and recovery

The output contains immutable configuration, resumable state, per-question
answers, runtime/tool traces, judges and a summary with accuracy, usage and
command/MCP utilization. Every record names the profile. Missing or failed
answers stay in the denominator and judge locally as wrong, matching the
canonical harness.

Provider or deployment infrastructure failure checkpoints completed work and
stops the command. Model-invalid, tool-invalid, policy-invalid and exhausted
items become visible per-question failures. A rerun with identical inputs
continues from the next missing answer or judge. Audit-write failure is fatal:
an unaudited Codex answer cannot be scored.

A result is invalid for comparison if review finds a native Codex command that
left `corpus/`, modified content, used network access, or sought gold/evaluator
artifacts. The implementation does not pretend shell-string inspection can
prove confinement. Codex summaries carry `audit_status=pending` until an
operator records `clean` or `invalid` with the small
`retrieval-ablation-review` command. A clean verdict additionally requires a
parseable runtime-audit entry for every answered item. There is no four-arm
comparison command, and documentation forbids treating `pending` or `invalid`
Codex output as a comparable result.

## 8. Cost, security and operational consequences

No profile triggers ingestion or P3 publication. `codex-*` answer calls report
subscription tokens and zero provider-reported marginal USD, with the existing
warning that zero is not free. `mcp-*` answers and every judge use the pinned
OpenRouter seats and shared reported-spend ceiling. All calls remain bounded by
explicit run-absolute ceilings.

The MCP arms exercise the public read contract and never expose write tools.
The Codex hybrid forwards credentials only through environment variables to
the local MCP subprocess. The deliberately instruction-only filesystem policy
is suitable for these operator-run development ablations, not for hostile
multi-tenant execution.

The pinned Codex SDK overlays `config_overrides` on the operator's existing
Codex configuration; it does not expose a public reset-to-empty option. The
runner therefore prevents any out-of-profile MCP action from becoming an
accepted answer and records it for review, but unrelated ambient tool schemas
may still be visible to the native model. Run the two Codex arms under the same
stable operator profile and disclose that limitation. Building a separate
Codex home would require copying or linking subscription credentials and is
not accepted in this experiment.

## 9. Delivery gates

The implementation is complete only when tests prove:

1. all four profiles expose exactly their declared access paths;
2. MCP definitions omit result schemas and write tools;
3. MCP error bits and decoded content drive trace, error and `Unknown`
   classification correctly;
4. P3 marker mismatch fails before a model call;
5. mocked native Codex runs reject zero-content, more than eight actions, web,
   file changes, subagents, dynamic tools and non-experiment MCP while auditing
   shell requests without result bodies for manual write/escape review;
6. the read-only STDIO server completes initialize/list/call/exit and refuses a
   direct ingest call;
7. source and ablation state remain separate and resume deterministically;
8. judge/scoring keeps failures in the denominator and reports route
   utilization plus Codex audit status;
9. hybrid execution revalidates and leaves the live document identity unchanged; and
10. Ruff, Pyright, import-linter and the focused/full test suites pass.
