# Unified `remember` Distribution, Container-First Engine, and Platform CLI (Design)

> **D136 amendment (2026-09-23).** Credentials, login, `remember mcp` modes and
> transports, and the entries `remember setup` writes are specified by
> [one_key_client_surfaces_design.md](one_key_client_surfaces_design.md): one signed
> key for every surface, `remember login` storing that one key, `remember mcp` with
> an engine mode (stdio or Streamable HTTP) and a bridge mode to a remote MCP URL,
> and one environment precedence shared by the SDK and CLI. §§2–4 below have been
> reconciled with it; where they are brief, that design is the contract.

> **D118 amendment (2026-09-07; effective when merged).** Ordinary autonomous adjudication can revise fact dates (§3); there is no separate temporal correction subsystem. D108 client and authority boundaries remain unchanged.
> [Authoritative contract and supersession map](mutable_fact_windows_design.md#10-authority-and-supersession-map).
> Conflicting temporal rules in the historical body below are superseded by that map.

> **Binding D108 decision (2026-09-05).** The single canonical package on PyPI is **`remember`**.
> The database and worker engine is distributed exclusively via Docker container images (`ghcr.io/writeitai/remember-stack:<tag>`)
> and Docker Compose. `rememberstack` is retired from standalone PyPI distribution. The binary command
> **`remember`** is owned exclusively by the `remember` package. Legacy human-review queue commands (`review`)
> and spend-ceiling inspection (`budget`) are retired from public client surfaces; autonomous bitemporal
> adjudication (D3/D43/D107) is the sole engine truth authority. The `remember` CLI operates as a developer
> and data-plane tool supporting authentication and project context (`login`, `whoami`, `switch`) and data-plane
> operations (`setup`, `ingest`, `query`, `mcp`), while administrative management tasks (`projects create`, `members list|invite`, `balance` billing top-ups) provide direct web console guidance to `https://remember.dev/app/...`.
>
> **D108 release-identity clarification (2026-09-22).** The repository remains
> `writeitai/remember-stack`, and all existing GitHub tags and releases remain
> historical records. New GitHub releases are titled `remember <version>` and
> attach only the canonical `remember` Python artifacts plus container deployment
> inputs. The canonical wheel installs no `rememberstack` executable. A terminal
> `rememberstack==0.17.0` forwarder is published to PyPI without being featured as
> a GitHub release asset, after which the PyPI project is archived and receives no
> further versions. The project is not deleted because deletion would break old
> pins and release the trusted name. Supporting analysis:
> `plan/analysis/remember_release_identity_and_pypi_retirement.md`.


---

## 1. Context & Problem Statement

### 1.1 The Dual-Package Friction
Prior to D108, the ecosystem maintained two packages with overlapping responsibilities:
1. **`rememberstack` on PyPI** (published up to `0.16.0`): Conceived in D62 as a monolithic package hosting client code, CLI, and engine server extras (`[server]`).
2. **`remember` on PyPI** (published up to `0.3.0`): Conceived as a lightweight client SDK.

This split introduced fundamental developer and agent friction:
- **Consumer Confusion**: Users and AI coding agents routinely questioned whether to install `remember` or `rememberstack`, frequently importing the wrong package or attempting to run server-side extras locally.
- **Binary Collision**: `rememberstack` registered `[project.scripts] remember = "rememberstack.surfaces.cli:main"`. If both packages were installed into the same environment, binary resolution was non-deterministic.
- **Heavyweight Host Burden**: Self-hosting a distributed bitemporal memory system requires PostgreSQL 19 with SQL/PGQ, `pgvector`, MinIO object storage, and complex C-extensions (`pglast`, `psycopg`, `pyarrow`). Attempting to install the server directly via `pip install rememberstack[server]` on bare-metal developer machines led to compiler, header, and architecture incompatibilities.

### 1.2 Real-World Infrastructure Industry Alignment
Modern cloud-plus-open-source developer tools (e.g. **Supabase**, **Sentry**, **PostHog**, **Temporal**) have converged on a single standard pattern:
- **PyPI / npm package**: A lightweight, sub-second install containing the client SDK, harness bootstrapper, and platform CLI (`supabase`, `sentry-sdk`, `posthog`, `temporalio`).
- **Engine Server**: Distributed exclusively as a pre-built container image via GitHub Container Registry (GHCR) orchestrated by Docker Compose or Helm/Kubernetes. Nobody pip-installs database servers on bare metal.

---

## 2. The Delivery Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  PyPI: `remember` (Sole Public Python Distribution)                          │
│                                                                              │
│  - Python SDK: `from remember import RememberClient`                         │
│  - Platform CLI: `remember` (setup, login, balance, projects, ingest, query)  │
│  - MCP Adapter: `remember mcp` (stdio & Streamable HTTP)                     │
│  - Dependencies: `httpx>=0.28.1`, `pydantic>=2.11`, `pydantic-settings>=2.10` (zero server dependencies)│
│  - Install footprint: < 2 MB, sub-second installation                        │
└──────────────────────────────────────────────────────────────────────────────┘
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
┌─────────────────────────────────────┐ ┌──────────────────────────────────────┐
│ Key issuer (remember.dev or other)  │ │ Data Plane (Managed or Self-Hosted)  │
│ - Signed keys, device-grant login   │ │ - HTTPS Ingest & Chunking            │
│ - Tenant balance & billing status   │ │ - Assured Retrieval Context          │
│ - Project & Member management       │ │ - Autonomous Bitemporal Memory       │
└─────────────────────────────────────┘ └──────────────────────────────────────┘
                                                         ▲
                                                         │
┌────────────────────────────────────────────────────────┴─────────────────────┐
│  GHCR: `ghcr.io/writeitai/remember-stack:<version>` (Docker Engine)           │
│                                                                              │
│  - The complete containerized engine: FastAPI daemon + E0–E3 workers         │
│  - Orchestrated via official `docker-compose.yaml` (Postgres 19, MinIO)       │
│  - Consumed by: Self-hosters, CI test harnesses, and Cloud deployment fleet  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Artifact Matrix

| Artifact | Distribution Channel | Target Audience | Primary Contents |
| :--- | :--- | :--- | :--- |
| **`remember`** | PyPI (`pip install remember`, `uvx remember`) | Developers, Application Code, AI Coding Agents | Client SDK facade, harness bootstrapper, MCP server, platform control CLI. |
| **`ghcr.io/writeitai/remember-stack`** | GHCR Container Image | Self-Hosters, SREs, Cloud Fleet | Headless engine server, worker execution loops, Alembic migrations. |
| **`remember-stack`** | GitHub Repository (`writeitai/remember-stack`) | Contributors, Maintainers, Auditors | Unified codebase: engine, workers, spine, client package, documentation website, and benchmarks. |

---

## 3. Platform CLI Taxonomy

The `remember` binary becomes the single command-line interface for the entire Remember platform. Commands are categorized strictly by **authority and transport**.

### 3.1 Account Commands (the key issuer)
All commands use the one signed key stored by `remember login` (D136). The key issuer
is discovered from its OAuth metadata; the default issuer is `https://remember.dev`
and `--issuer` / `REMEMBER_ISSUER` selects another. Account behaviour itself is
defined by the issuer (for remember.dev, the cloud design).
- **`remember login`**: Runs the device grant against the issuer, lets the person choose projects and permissions on the issuer's consent page, and stores the one key in the owner-only `0600` credential file (`~/.config/remember/credentials.json`, version 2).
- **`remember logout`**: Revokes the stored key at the issuer and removes the file.
- **`remember whoami`**: Displays the key's issuer, id, expiry, default project and permissions, then the issuer's account view when the key has `account:read`.
- **`remember balance`** (or `remember billing`): Directs operators to the cloud console at `https://remember.dev/app/billing` to view balances and top up credits.
- **`remember projects list`**: Enumerates the projects the key covers.
- **`remember projects create <name>`**: Directs operators to the cloud console at `https://remember.dev/app/projects` to provision new projects (exit code 1).
- **`remember switch <project>`**: Sets the default project in local configuration; no new credential is minted, because the one key already covers its projects.
- **`remember members list`** / **`remember members invite <email>`**: Directs operators to the cloud console at `https://remember.dev/app/team` to manage team organization seats (exit code 1).

*Self-Hosted Behavior*: When configured in self-hosted mode (`--self-hosted`), executing control-plane commands prints a clean, honest notice:
> *"Note: You are connected to a self-hosted engine (http://localhost:8000). Projects, team members, and billing are cloud-managed services on remember.dev."*

### 3.2 Data Plane Commands (`https://<tenant>.dp.remember.dev` or `http://localhost:8000`)
Authenticated with the one signed key (verified by the engine's signed-key perimeter, D136 §7) or a self-hosted pre-shared bearer secret (`API_BEARER_BIND`). With a signed key and no explicit URL, the data-plane host is resolved from the key (D136 §8.3):
- **`remember setup [OPTIONS]`**: The Sentry-like AI harness bootstrapper (see §4).
- **`remember ingest <path> [OPTIONS]`**: Streams markdown or source files through the E0 ingestion endpoint.
- **`remember query <text> [OPTIONS]`**: Executes assured context retrieval (`facts_context` or `combined_context`) and prints formatted JSON or markdown summaries.
- **`remember operations list|run`**: Discovers and invokes remote assured operations by name.
- **`remember mcp [OPTIONS]`**: Runs the Model Context Protocol (MCP) server. Engine mode serves the shared `remember.mcp_tools` catalogue against the resolved data plane over stdio or Streamable HTTP (`--transport http`); bridge mode relays stdio to a remote MCP URL (`--remote-url` / `REMEMBER_MCP_URL`) with the key. Specified in D136 §5.
- **`remember doctor`**: Verifies connectivity, auth token validity, tenant endpoint health, and agent harness configurations.

### 3.3 Retirement of Legacy Commands
- **`remember review` (Removed)**: The D24 human review queue commands (`review list`, `review decide`) are deleted from the CLI, including their internal-ops path. They were built for early prototype cluster curation. The production engine uses autonomous bitemporal adjudication (D3/D43/D107). Human review has been removed from all documentation and is not part of the public product.
- **`remember budget` (Removed)**: The command is deleted from the CLI, including its internal-ops path. Spend ceilings are an internal worker detail enforced by the workers; operators read spend with `remember ops cost-export`, and the platform exposes credits through `remember balance`.
- **`remember ops` (Confined to Internal/Docker)**: SRE tasks (`ops replay`, `ops rebuild`) are executed inside the server container environment via internal scripts, not exposed on the developer client CLI.

### 3.4 Credential Architecture (D136)
One credential serves every surface: a signed key minted by the issuer (or, for a
self-hosted engine without an issuer, the pre-shared bearer secret). The credential
file holds that one key; the SDK and CLI resolve the key, engine URL and project with
one shared precedence (explicit value, then `REMEMBER_API_KEY` / `REMEMBER_API_URL` /
`REMEMBER_PROJECT`, then the stored credential file (read by SDK and CLI alike), then derivation from the
key). A key from the file is sent only to its issuer, its recorded URL, or a
data-plane URL the issuer resolved for it. The file format, resolution table and
failure behaviour are in
[one_key_client_surfaces_design.md §8](one_key_client_surfaces_design.md#8-sdk-and-cli-with-one-key).

Self-hosted bearer secrets grant full authority over the instance and receive the
same confidentiality protections as issued keys: never committed, `0600` on disk,
and passed to harnesses only by environment reference.

---

## 4. Coding Agent Bootstrapper (`remember setup`)

### 4.1 Invocation Grammar
```bash
remember setup [OPTIONS]

Options:
  --cloud                          Configure for the key issuer (default; runs `remember login` if no key is stored)
  --issuer URL                     Non-default key issuer
  --self-hosted                    Configure for local self-hosted engine (http://localhost:8000)
  --api-url TEXT                   Explicit engine URL (selects a self-hosted engine-mode entry)
  --mcp-url TEXT                   Remote MCP URL of a self-hoster's `remember mcp --transport http`
  --api-key TEXT                   Explicit key (discouraged; ambient login preferred)
  --headless                       Prefer a key-header remote entry over OAuth sign-in
  --agent [cursor|claude|codex|agy|all]  Target specific harness (auto-detects all present if omitted)
  --dry-run                        Preview file modifications without writing
  --help                           Show this message and exit
```

### 4.2 Credential Safety & Invariants (D92 / D108)
1. **Zero Secret Leakage in Git**: Project-local configuration files (`.cursor/mcp.json`, `.agents/mcp_config.json`, `.codex/config.toml`) must **never** embed plaintext API tokens.
2. **Ambient Credential Resolution**:
   - The CLI executable resolves credentials dynamically at runtime via the precedence cascade in §3.4.
   - Injected harness configuration is one of the D136 §6 entry shapes: a remote entry with OAuth sign-in (no secret), a remote entry whose bearer header references `REMEMBER_API_KEY`, a stdio bridge (`<launcher> mcp` with `REMEMBER_MCP_URL`), or a self-hosted engine-mode entry (`<launcher> mcp` with `REMEMBER_API_URL`).

### 4.3 Durable Harness Launcher Strategy (`uvx` vs System PATH)
A common pitfall with ephemeral package runners and GUI editors (e.g. Cursor, VS Code, Claude Desktop) is that GUI applications on macOS and Linux are launched with a minimal system environment (`PATH=/usr/bin:/bin`) that does not inherit interactive shell paths (such as `~/.local/bin` where `uvx` and `uv` reside, or an active virtualenv). Naively emitting bare `remember` or bare `uvx` results in immediate `FileNotFoundError` upon editor restart or background agent invocation.

To guarantee zero-friction, permanent launcher operation across all harness execution contexts:
1. **Durable Binary Resolution & Absolute Normalization**:
   - `remember setup` locates launcher candidates and strictly normalizes and validates them into canonical absolute paths (`Path(candidate).resolve().as_posix()`), explicitly rejecting relative paths to ensure executions remain valid regardless of the process working directory:
     - `candidate = shutil.which("remember")` -> `resolved_remember = Path(candidate).resolve().as_posix() if candidate else None`
     - `candidate_uvx = shutil.which("uvx")` -> `resolved_uvx = Path(candidate_uvx).resolve().as_posix() if candidate_uvx else None`
   - If neither candidate can be resolved to a verified absolute executable, `remember setup` halts with clear operator instructions to install `uv` or run `uv tool install remember`.
2. **Universal Absolute Launcher Emission**:
   - **When `remember` is installed on persistent system PATH**:
     - Emits the fully-resolved, canonical absolute binary path:
       - `"command": resolved_remember` (e.g. `"/usr/local/bin/remember"` or `"/opt/homebrew/bin/remember"`).
       - `"args": ["mcp"]`.
   - **When invoked via `uvx` or when `remember` is not globally installed**:
     - Emits the fully-resolved, canonical absolute binary path to `uvx`:
       ```json
       {
         "command": "/Users/<user>/.local/bin/uvx",
         "args": ["remember", "mcp"]
       }
       ```
   - **For CLI Registration Harnesses (e.g. Claude Code CLI)**:
     - Always registers with the resolved absolute path (`claude mcp add remember -- <resolved_launcher_path> [remember] mcp`) so that subsequent Claude sessions launched from IDE extensions or minimal-PATH environments do not fail when the registering shell's PATH is absent.
   - **Environment Guarding**:
     - For harnesses supporting explicit environment blocks (e.g. Cursor, Claude Desktop), optionally injects the resolved directory into `"env": { "PATH": "..." }` to safeguard child processes.
   - Outputs a friendly hint:
     > *"Tip: Run `uv tool install remember` to install the `remember` CLI permanently to your shell PATH."*

### 4.4 Harness Configuration Matrix

Which entry shape a harness receives (remote or local launcher) follows D136 §6; the table lists the launcher form used whenever a local entry is written.

| Harness | Detection Trigger | Configuration Action | Launch Command Emitted |
| :--- | :--- | :--- | :--- |
| **Cursor** | `.cursor/` directory exists | 1. Merges `remember` into `.cursor/mcp.json`<br>2. Writes `.cursor/rules/remember.mdc` | `"command": "<resolved_launcher_path>"` |
| **Claude Code** | `claude` CLI on PATH | Executes native `claude mcp add` registration | `claude mcp add remember -- <resolved_launcher_path> [remember] mcp` |
| **Claude Desktop** | `claude_desktop_config.json` exists | Merges `mcpServers.remember` into configuration | `"command": "<resolved_launcher_path>"` |
| **Codex** | `.codex/` or `config.toml` exists | Configures `[mcp_servers.remember]` table in `config.toml` | `command = "<resolved_launcher_path>"` |
| **Antigravity** | `.agents/` directory exists | Writes `.agents/skills/remember/SKILL.md` and updates `mcp_config.json` | `"command": "<resolved_launcher_path>"` |

*Note on `<resolved_launcher_path>`*: In all configurations, `<resolved_launcher_path>` is strictly the resolved absolute path to the binary (e.g. `/Users/<user>/.local/bin/uvx` with args `["remember", "mcp"]`, or `/usr/local/bin/remember` with args `["mcp"]`). Bare executable names are never emitted.

---

## 5. Contract & Parity Governance

Decoupling client delivery from internal engine implementation requires strict, automated contract verification to prevent silent wire drift.

### 5.1 Three-Pillar Contract Verification
1. **OpenAPI Schema Governance**:
   - The engine exports `openapi.json` offline via `scripts/export_openapi.py`.
   - Engine CI enforces that `openapi.json` strictly matches `http_api.py` without uncommitted drift (`test_the_checked_in_schema_matches_the_app`).
   - Client wire models in `remember.client` are generated from or continuously tested against `openapi.json`.
2. **Dynamic Operation Descriptors (`GET /operations`)**:
   - OpenAPI defines HTTP routes, but Remember retrieval tools rely on dynamic operation descriptors (`ToolDescriptor`).
   - Client test suites execute live verification against both mock and integration endpoints to validate argument validation, error envelopes, and bitemporal response shapes.
3. **Cross-Version Integration Matrix**:
   - CI runs matrix tests pairing candidate client builds against:
     - Released Docker engine versions (`0.15.0`, `0.16.0`).
     - Live candidate engine branch.
   - Verifies backwards and forwards compatibility of the wire protocol.

---

## 6. Migration & Deprecation Plan

1. **Phase 1: Package Reorganization in `remember-stack`**:
   - Restructure repository root to house the `remember` client package alongside engine modules (or clean `packages/remember` layout).
   - Ensure `pyproject.toml` for `remember` declares only `httpx`, `pydantic`, and `pydantic-settings`.
2. **Phase 2: PyPI Cutover**:
   - Release `remember 0.17.0` owning the `remember` CLI binary and presenting `remember 0.17.0` as the GitHub release title.
   - Release `rememberstack 0.17.0` once as an inactive PyPI-only transition forwarder with a visible warning directing users to `remember` for client usage and Docker Compose for server deployments.
   - Archive the `rememberstack` PyPI project after publication. Do not delete it, attach it to the GitHub release, or publish another version.
3. **Phase 3: Docs & Quickstart Updates**:
   - Update `remember.dev/docs` and `docs.remember.dev` quickstart guides to feature `uvx remember setup` as the primary onboarding command.
   - Deprecate bare-metal pip install instructions for self-hosters; standardize all self-hosted guides on Docker Compose.

---

## 7. Consequences & Preserved Invariants

- **D92 & D108 (Secret Isolation)**: API tokens and bearer secrets (both cloud and self-hosted) are never written into committed git repositories or project-local files.
- **D43 (Autonomous Bitemporal Memory)**: All recall and truth adjudication operates autonomously without blocking on human queues.
- **D66 (Honest Status & Balance)**: Balance and credit transparency is maintained across both web UI and CLI (`remember balance`).
- **D92 (Credential Storage)**: The credential file holds one signed key, written by `remember login` and read by SDK and CLI under the stored-key origin rule (D136).
- **Durable Zero-Friction Onboarding**: `uvx remember setup` automatically configures persistent, absolute-path launchers (`<resolved_launcher_path> [remember] mcp`) that operate seamlessly without manual `$PATH` intervention or GUI editor `FileNotFoundError` failures.
- **Zero Host-Dependency Friction**: Developers and AI agents never encounter C-extension compilation errors when adopting Remember.
