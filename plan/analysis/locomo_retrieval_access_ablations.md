# LoCoMo retrieval-access ablations

**Status:** analysis; not a benchmark result or product contract.
**Date:** 2026-09-14.
**Feeds:** `plan/designs/locomo_retrieval_ablation_design.md`.

## Question

The current LoCoMo answer loop gives a model a benchmark-owned union of 21
tools. Four assured-operation descriptors dominate its prompt because their
complete result schemas are repeated on every model turn. This is unlike an
ordinary MCP client, where `tools/list` supplies only a name, description and
input schema. It also prevents us from separating two useful questions:

1. how much can an agent answer from the navigable P3 corpus alone; and
2. what additional value comes from RememberStack retrieval through the
   product's MCP surface?

The owner wants four inexpensive answer-only experiments over one processed
LoCoMo conversation:

| Arm | Answer runtime | RememberStack | P3 |
| --- | --- | --- | --- |
| Codex + P3 | native Codex agent | none | local filesystem |
| Codex + P3 + MCP | native Codex agent | official OSS MCP | local filesystem |
| MCP | provider-neutral LoCoMo agent | official OSS MCP | none |
| MCP + P3 tools | provider-neutral LoCoMo agent | official OSS MCP | list/search/read tools |

This is a crossed experiment, not one clean four-way ranking and not a
replacement for the frozen publication protocol. The causal comparisons are
pairwise: Codex + P3 versus Codex + P3 + MCP measures adding MCP to the native
Codex route; MCP versus MCP + P3 tools measures adding bounded P3 access to the
provider-neutral route. Cross-family scores are directional because the agent
loop and P3 access mechanism also differ. Every arm must use the same processed
store, P3 snapshot where applicable, selected questions, answer policy, Luna
model family and judge.

## What exists now

### The answer catalog is not the product MCP catalog

`benchmarks/locomo/retrieval.py` constructs a 21-tool catalog from four
sources: assured operations, seven direct SDK primitives, seven open-query
descriptors and three benchmark-local P3 tools. The model emits a structured
`AnswerAgentStep`; the runner dispatches it through `MemoryClient` or
`P3Mount`. No MCP transport participates in that loop.

The remote OSS MCP server in `src/remember/remote_mcp.py` already provides the
customer-facing composition authority. Its `tools/list` response contains
only `name`, `description` and `inputSchema`; `tools/call` returns one JSON
text content block and an `isError` bit. It also advertises ingest and pipeline
readiness, which an answer agent must not receive.

### Most repeated prompt text is result schemas

At current main, canonical JSON for the 21 benchmark descriptors is about
130,000 characters. The four assured descriptors account for about 120,000
characters, of which about 116,000 are result schemas. Those schemas are
useful for validating a product response but are unnecessary in an agent's
up-front tool definition. MCP's smaller descriptor shape therefore addresses
the largest fixed prompt cost without deleting retrieval capabilities from the
product.

A prior 20-question conv-42 Codex answer pass constructed about 9.4 million
prompt characters. Roughly 81% was the repeated catalog and roughly 17% was
the accumulating tool trace. Changing to MCP-shaped definitions attacks the
first cost. It does not make full evidence envelopes small, so response
compaction remains a separate product question. The four requested arms contain
no legacy 21-tool control, so they will not by themselves prove that the
canonical catalog can be replaced or attribute a quality change solely to the
missing result schemas. The measurements motivate this experiment; they are not
its dependent variable.

### P3 is already an ordinary directory

The benchmark already requires the published P3 directory and checks its
`.snapshot-version` against live readiness. The sharding restore path downloads
and verifies a retained run, including its P3 publication, into an ordinary
local directory. The experiment therefore needs no UMC service, RememberFS or
FUSE mount. An operator may use the restored P3 directory or copy the exact
published directory from the benchmark host; the marker is the identity check.

### Codex can use local files and STDIO MCP

The pinned `openai-codex` SDK starts the official app-server runtime and accepts
a working directory plus configuration overrides. Codex supports local STDIO
MCP servers. The existing subscription adapter deliberately uses an empty
directory and rejects every runtime action, so it cannot be reused unchanged
for the two native-agent arms. Its authentication and audit mechanics are,
however, the right base: app-server owns the ChatGPT login, each question gets
an ephemeral thread, and returned action metadata is recorded without tool
result bodies.

Official OpenAI documentation consulted 2026-09-14:

- [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
  documents local STDIO servers, per-server tool allowlists and required
  startup.
- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) documents programmatic
  threads over the Codex runtime.

## Alternatives

### Four new full protocols

Rejected. A full protocol owns ingestion and its immutable run state. Four
protocols would either process the conversation four times or require an
unsafe shortcut that aliases one store into unrelated protocol fingerprints.
The resulting scores would confound retrieval access with processing output.

### Replace the canonical 21-tool loop immediately

Rejected for this change. It would roll the publication protocol before the
four-way evidence exists and would mix a cost correction with a benchmark
definition change. The ablation can exercise the smaller customer-facing MCP
surface first.

### Give every arm unrestricted shell access

Rejected. Shell is the thing being measured in the Codex/P3 arms, not a neutral
transport for the MCP arms. Giving it to the latter would make the access
profiles indistinguishable.

### Build RememberFS or a new download service first

Rejected. P3 is already a local directory after the existing restore/copy
step. A managed mount is a separate cloud-product concern and would add an
unrelated dependency to an OSS retrieval experiment.

### Hard sandbox and cap shell output

Not chosen for these development experiments. The owner prefers a simple
instruction-and-audit contract: do not use the Internet, do not modify the
corpus, and do not leave the supplied working directory. Every native Codex
runtime action remains visible, and a violating run is invalid rather than
silently treated as a score. This is not a claim that prompt instructions are
a security boundary.

## Recommendation

Add a resumable, explicitly experimental `retrieval-ablation` command. It
reads one prepared and processed run but writes a separate output directory, so
the canonical `state.json` is never changed. Its configuration binds the
source protocol fingerprint, source processing revision, ablation-runner
revision, sample, profile, model, prompt/schema hashes, MCP catalog hash, P3
version when used and judge pins. This allows the new runner to examine a store
produced by the immediately preceding source revision without pretending the
two revisions are the same program.

The provider-neutral arms should obtain their definitions and results from the
actual `RemoteOperationMcpServer` implementation in a new read-only composition
that does not advertise or dispatch ingest/readiness. The optional P3 tools
should use the same MCP descriptor and result shape. This is an in-process call
to `list_tools()` / `call_tool()`: it exercises the product MCP semantics without
paying for JSON-RPC framing. The model-visible catalog intentionally excludes
the seven direct SDK primitives from the canonical 21-tool benchmark catalog.

The native Codex arms should run one ephemeral thread per question under
`ApprovalMode.deny_all` and `Sandbox.full_access`. A fresh working directory has
only a `corpus/` link to the validated local P3 tree, rather than the source run
or dataset. The hybrid arm adds one required read-only STDIO server launched by
`sys.executable -m remember mcp --read-only`; Codex configuration overrides pin
that server and forward credential variable *names*, never values. Both arms
record command and MCP request metadata; web, file-change, subagent,
non-RememberStack MCP and other action types invalidate the answer. Commands
remain allowed because filesystem search is the P3-only arm's retrieval
mechanism. The audit deliberately omits returned content to avoid duplicating
the corpus into logs, caps runtime actions at eight, and reports shell/MCP
utilization separately.

All answer arms use the Luna model family at high reasoning effort: native
Codex uses `gpt-5.6-luna` through the ChatGPT subscription and the
provider-neutral arms use `openai/gpt-5.6-luna` through OpenRouter. Provider and
agent-runtime differences remain and are disclosed; Gemma and source-protocol
provider inheritance are not extra axes in this experiment. The judge is the
canonical Full-v31 OpenRouter Luna judge.

All four arms use the same concise-answer, temporal, evidence-authority,
entity-follow-up, counterfactual and completeness rules. Route-specific text
explains only the available access path. The judge prompt and gold data remain
unchanged. Results are labelled experimental and are not mergeable into a
canonical publication summary.
