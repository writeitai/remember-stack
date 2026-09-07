# Workspace-Bench Codex-subscription cloud/local smoke

Experimental adapter. It is **not** an upstream leaderboard protocol. Task 300
is the first live smoke because its input/output extension classes cover
`.ppt`, `.xls`, `.xlsx`, `.json`, `.txt` → `.md`, `.csv`, `.json`. Models do
not hard-code those filenames.

Install the repository plus the Codex runtime pin:

```bash
uv sync --extra benchmark
```

External paths must be absolute. Nothing in this adapter downloads the
18.7 GB workspace archive, starts cloud ingest, or calls a paid model unless
the operator passes `--execute` after a green preflight. `--execute` also
authorizes the live credential-isolation canary subscription turn.

`--workspace` is the complete pristine **role workspace**. `--task-dir` is the
separate **task corpus** (`metadata.json`, `data_manifest` files, optional
`data/`). The adapter clones the role workspace twice and stages manifest
inputs identically into both clones. Evaluator `metadata.json` is not present
in the task workspace or arm case directory during the tested Codex turn.

## Implemented and tested

| Command / behavior | Spend | Status |
| --- | --- | --- |
| `python -m benchmarks.workspacebench preflight` | none (optional local Codex `account/read`) | implemented; synthetic tests. MCP discovery launches `python -m remember mcp --read-only` over stdio and uses ambient `remember login` credentials. Structural canary only. |
| `python -m benchmarks.workspacebench run-agent` | none | dry envelopes only. CLI `--execute` is rejected; `run-pair` is the only supported live entry. Tests may still inject `run_agent`. |
| `python -m benchmarks.workspacebench run-pair` | live Codex only with `--execute` | implemented; tests inject stdio MCP, live canary, and turn runners. `--execute` requires `--task-dir`, cannot skip account/MCP/office, and requires a passing live canary before either task arm. |
| `remember mcp --read-only` | none | implemented; `tools/list` filters reserved write names even when an assured operation collides; direct hidden write calls stay rejected |
| Official judge command builder | none | implemented; requires `--task-dir` and `--eval-yaml`. Does **not** invoke the paid judge |
| Host-local office skill staging | none | implemented; copies each pinned `evaluation/skills/office/<name>` to workspace `.agents/skills/<name>` (discoverable `SKILL.md` children) and checks `soffice` / `pdftoppm` |

## Automatic live gates (enforced in-process, still no paid task/judge)

These fail closed on `run-pair --execute` without starting either task arm:

1. ChatGPT account attestation (`codex` account type `chatgpt`).
2. MCP catalog discovered over real stdio of the current `remember mcp --read-only`.
3. Live credential-isolation canary turn under the same `workspace-write` /
   deny-all / disposable-`CODEX_HOME` / OS-keyring boundary, using the protocol
   model and effort, the exact memory arm, and the supervised production
   runner. A fake secret is stored outside an empty disposable workspace. The
   gate passes only when a completed turn contains an observed command that
   names that path and the read is denied with a nonzero exit, with no secret
   in events or output. `--skip-account`, `--skip-mcp`, and `--skip-office`
   cannot bypass these gates.

Dry `preflight` remains no-spend and labels its canary as **structural**.

## Still-external live gates (not executed here)

1. Cloud ingest of the complete Backend Developer workspace and production of a
   sealed receipt (no bearer token in the receipt).
2. Possession of the pinned Lite task corpus and English workspace archive.
3. A live ChatGPT-subscription **task** turn for each arm (authorized by
   `--execute`, not invoked by tests).
4. Official Anthropic-compatible judge calls (`--task-dir` + `--eval-yaml`).
5. Docker-container equivalence. This smoke path is **host-local**. The
   protocol refuses to copy `~/.codex/auth.json` into a container, so container
   parity is an unsatisfied publication gate.

## Operator steps

### 1. External dataset / workspace preparation

Not implemented as a downloader. Obtain the pinned Workspace-Bench checkout at
`3fbd0f1a136720fece86786545983e26642c3db2` and the English role workspace
separately. Preflight verifies the checkout commit, cleanliness, and hashed
post-leakage runner files. It never mutates that checkout.

### 2. Cloud ingest and receipt production

Handoff only (future cloud job; this adapter consumes the receipt):

1. Inventory and hash the complete role workspace (`tree_digest`).
2. Ingest through ordinary E0 with the pinned conversion/router configuration.
3. Wait for the declared version IDs and required readiness coordinates
   (`pipeline`, `p1`, and `live_graph`, which together support the four
   assured read operations `resolve_entity`, `claims_and_sources_context`,
   `facts_context`, and `combined_context`; `p3`/CorpusFS is optional).
4. Seal the deployment read-only for task execution.
5. Write an operator-attested `WorkspaceBenchCloudReceipt/v1` JSON **without**
   a bearer token.
6. Establish an SSH local forward or HTTPS endpoint before local preflight.

Receipt fields: `workspace_digest`, `rememberstack_revision`,
`converter_router_configuration`, `component_generations`, `version_ids`,
`readiness_requirements`, `api_origin`, `deployment_id`, `sealed`,
`attested_by`, `attested_at`.

`api_origin` on the receipt is the **canonical deployment origin**. A local
SSH forward is a different origin and must be declared as a typed access
binding (`--access-mode ssh_local_forward --canonical-origin <receipt origin>
--api-url http://127.0.0.1:18000`). Direct HTTPS access must match the receipt
origin exactly. Origin validation is not disabled.

### 3. Local Codex login (OS keyring required)

`codex-cli` 0.147.0 `app-server` does **not** accept `--ignore-user-config`
(bundled app-server help/launch rejects it with exit 2). Live isolation is
therefore:

1. ChatGPT credentials stored in the **OS keyring**, not a file-backed
   `auth.json` cache;
2. a disposable `CODEX_HOME` with a minimal config that declares no MCP
   servers, hooks, plugins, skills, or global instructions;
3. `--config cli_auth_credentials_store="keyring"` on the pinned bundled
   binary so user `~/.codex` cannot change the treatment.

Migrate once on the operator machine (this writes to the OS keyring via the
official CLI; the adapter never reads or copies `auth.json`):

```bash
# In ~/.codex/config.toml (operator machine only; not copied into the run):
#   cli_auth_credentials_store = "keyring"
codex login
codex login status
```

If `codex login status` only succeeds against a file-backed cache, preflight
and `--execute` fail closed. The adapter asks the official runtime for account
type `chatgpt` under the disposable home. It never reads, copies, parses,
logs, or names `auth.json` in prompts, workspaces, argv, traces, or artifacts.

### 4. Tunnel and ambient Remember login

```bash
ssh -N -L 18000:127.0.0.1:8000 user@remember-host
remember login --api-url http://127.0.0.1:18000
```

Device grant still talks to `--token-host` (default `https://api.remember.dev`).
`--api-url` only overrides the advertised data-plane origin so MCP can target
the tunnel.

Ambient query-only token preferred. The Workspace-Bench CLI does **not**
construct a `MemoryClient` and does **not** pass a token in argv, environment,
prompt, workspace, or artifacts. Spawned Remember MCP and Codex app-server
processes receive a sanitized environment (executables, TLS, locale, `HOME`,
and — memory arm only — Remember config-dir discovery). Ambient `CODEX_HOME`
is dropped and replaced with the disposable home. The openai-codex 0.147.0
SDK copies `os.environ` and then updates `CodexConfig.env` before `Popen`.
This adapter does not mutate the parent process environment. Account
attestation, the live canary, and task runs launch the pinned bundled binary
through a fixed `/usr/bin/env -i KEY=VALUE ...` prefix so the Codex
app-server, model commands, and MCP child receive only the allowlisted
environment. The trusted env process may momentarily inherit the parent
environment; argv contains no secrets and no auth-cache path. They keep using
the operator's credential stores (keyring for Codex, `remember login` files
for MCP); this adapter does not read or copy those files.
`remember mcp --read-only --api-url …` resolves `remember login` credentials
itself.

### 5. No-spend preflight (implemented)

Direct HTTPS:

```bash
uv run --extra benchmark python -m benchmarks.workspacebench preflight \
  --upstream /abs/path/Workspace-Bench \
  --workspace /abs/path/backend-developer-workspace \
  --task-dir /abs/path/Workspace-Bench/evaluation/tasks_lite/300 \
  --receipt /abs/path/cloud-receipt.json \
  --api-url https://remember.example.test \
  --access-mode direct \
  --output /abs/path/wb-runs/task-300/preflight \
  --task-id 300
```

SSH local forward (canonical origin remains the receipt origin):

```bash
uv run --extra benchmark python -m benchmarks.workspacebench preflight \
  --upstream /abs/path/Workspace-Bench \
  --workspace /abs/path/backend-developer-workspace \
  --task-dir /abs/path/Workspace-Bench/evaluation/tasks_lite/300 \
  --receipt /abs/path/cloud-receipt.json \
  --api-url http://127.0.0.1:18000 \
  --access-mode ssh_local_forward \
  --canonical-origin https://remember.example.test \
  --output /abs/path/wb-runs/task-300/preflight \
  --task-id 300
```

### 6. Native and memory agent execution (implemented; live with `--execute`)

```bash
uv run --extra benchmark python -m benchmarks.workspacebench run-pair \
  --upstream /abs/path/Workspace-Bench \
  --workspace /abs/path/backend-developer-workspace \
  --task-dir /abs/path/Workspace-Bench/evaluation/tasks_lite/300 \
  --receipt /abs/path/cloud-receipt.json \
  --api-url http://127.0.0.1:18000 \
  --access-mode ssh_local_forward \
  --canonical-origin https://remember.example.test \
  --eval-yaml /abs/path/Workspace-Bench/evaluation/runs/judge.yaml \
  --output /abs/path/wb-runs/task-300/pair \
  --task-id 300 \
  --arm-order native,memory \
  --execute
```

Without `--execute` the command still preflights and writes `not_executed`
envelopes. That is the synthetic/dry path. `run-agent --execute` is not a
supported live entry.

The tested prompt receives the pinned Workspace-Bench working-directory and
final Python path-list requirements, with target output directory
`model_output`. Outputs are collected from the final response path list,
expected output basenames, that target directory, and a safe changed-file
fallback, then copied into each arm case `output/` directory. Partial
artifacts are preserved. Full evaluator `metadata.json` and `agent.json` are
written only after the tested agent exits.

The openai-codex 0.147.0 synchronous `thread.run` call has no benchmark
wall-clock deadline. An external process-group supervisor terminates the
child. Partial runtime events are retained only if the child flushed a turn
receipt before SIGTERM; the SDK itself does not return partial items after an
external kill. Supervisor request/turn temp files are always removed.

### 7. Local official judge (handoff, live gate)

The official scorer CLI at the pin requires **both** `--task-dir` and
`--eval-yaml`. It prepares its own restricted judge view from case output,
post-turn metadata, source task `data/`, and `agent.json`. Copy-paste after a
pair run:

```bash
python3 /abs/path/Workspace-Bench/evaluation/src/agent_as_a_judge.py \
  --task-dir /abs/path/wb-runs/task-300/pair/native \
  --eval-yaml /abs/path/Workspace-Bench/evaluation/runs/judge.yaml

python3 /abs/path/Workspace-Bench/evaluation/src/agent_as_a_judge.py \
  --task-dir /abs/path/wb-runs/task-300/pair/memory \
  --eval-yaml /abs/path/Workspace-Bench/evaluation/runs/judge.yaml
```

`run-pair --eval-yaml` prints those commands. This repository does not call
the judge model. A Codex-subscription judge is a different experimental
protocol and is out of scope.

### 8. Report generation (implemented)

`run-pair` writes `paired-report.json` by reconstructing the two arm receipts.
Reconstruction checks the pair protocol fingerprint and each arm execution
fingerprint, not only task IDs and workspace digests. The protocol fingerprint
includes the observed SHA-256 of the pinned bundled `codex-cli` 0.147.0
binary, passed from preflight into every rebuilt pair/result protocol. Live
`--execute` fails closed if that pin is missing or the hash changes between
preflight and execution. The digest is platform-specific (macOS and Linux
wheels of the same CLI version differ); compare fingerprints only within the
same runtime pin. The report is labeled experimental and does not infer
benchmark quality from a single task.

## Dry smoke

Tests under `src/tests/benchmarks/test_workspacebench_*.py` use a tiny synthetic
workspace with the same input/output *extension classes* as task 300. They do
not download the corpus, call Codex, invoke the official judge, or hit a live
RememberStack deployment.

## Credential rule

Credentials must not appear in prompts, workspaces, generated Codex config,
command-line arguments, traces, result envelopes, or judge views. Remember MCP
uses ambient operator credentials. The memory arm registers
`python -m remember mcp --read-only` with `enabled_tools` bound to the
preflight stdio catalog. The native arm registers no Remember MCP server and
no Remember config-dir variables. User Codex config, global instructions,
hooks, plugins, skills, and arbitrary user MCP cannot enter either arm: the
run uses a disposable `CODEX_HOME` plus OS-keyring ChatGPT credentials, not
`--ignore-user-config` (bundled `app-server` rejects that flag). Office
skills are staged into `.agents/skills/<skill-name>` so Codex discovers each
`SKILL.md` as a direct child of the task workspace. Existing role-workspace
skills are preserved; a conflicting non-identical name fails closed. Both arms
receive byte-identical staged workspaces. Path topology is validated before
any output directory is created: task-dir may nest under the pinned upstream
checkout; workspace and output must stay disjoint from each other and from
every source root. This path does not claim Docker equivalence.
