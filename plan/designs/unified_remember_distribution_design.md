# Unified `remember` Distribution, Container-First Engine, and Platform CLI (Design)

> **Binding D110 amendment (2026-09-07).** D110 §§2, 4 supply the explicit autonomous temporal-correction contract that reconciles D108 authority with the superseded human mechanism in D107 §4.3. Existing plane adjudication logs record decisions; uncertainty does not create a public human queue.
> Contract: [temporal writes and lifecycle](temporal_write_and_lifecycle_design.md).

> **Binding D108 decision (2026-09-05).** The single canonical package on PyPI is **`remember`**.
> The database and worker engine is distributed exclusively via Docker container images (`ghcr.io/writeitai/remember-stack:<tag>`)
> and Docker Compose. `rememberstack` is retired from standalone PyPI distribution. The binary command
> **`remember`** is owned exclusively by the `remember` package. Legacy human-review queue commands (`review`)
> and spend-ceiling inspection (`budget`) are retired from public client surfaces; autonomous bitemporal
> adjudication (D3/D43/D107) is the sole engine truth authority. The `remember` CLI operates as a developer
> and data-plane tool supporting authentication and project context (`login`, `whoami`, `switch`) and data-plane
> operations (`setup`, `ingest`, `query`, `mcp`), while administrative management tasks (`projects create`, `members list|invite`, `balance` billing top-ups) provide direct web console guidance to `https://remember.dev/app/...`.


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
│ Control Plane (api.remember.dev)    │ │ Data Plane (Managed or Self-Hosted)  │
│ - OAuth device-grant authentication │ │ - HTTPS Ingest & Chunking            │
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

### 3.1 Control Plane Commands (`https://api.remember.dev`)
Authenticated via user session credentials obtained through OAuth device-grant login:
- **`remember login`**: Initiates device-code OAuth flow against `https://api.remember.dev` (or explicit `--control-plane-url`), prompts in terminal or opens browser, receives user session credentials and active tenant bindings, and writes owner-only `0600` credential file to `~/.config/remember/credentials.json`.
- **`remember logout`**: Idempotently revokes the active session token at the control plane and removes stored credentials.
- **`remember whoami`**: Displays authenticated identity, active organization, and current project context.
- **`remember balance`** (or `remember billing`): Directs operators to the cloud console at `https://remember.dev/app/billing` to view balances and top up credits (reports status code 1 when no control token is configured).
- **`remember projects list`**: Enumerates available tenant deployments in the user's organization.
- **`remember projects create <name>`**: Directs operators to the cloud console at `https://remember.dev/app/projects` to provision new tenant projects (exit code 1).
- **`remember switch <project>`**: Sets the default active project and selects its corresponding tenant data-plane token in local configuration.
- **`remember members list`** / **`remember members invite <email>`**: Directs operators to the cloud console at `https://remember.dev/app/team` to manage team organization seats (exit code 1).

*Self-Hosted Behavior*: When configured in self-hosted mode (`--self-hosted`), executing control-plane commands prints a clean, honest notice:
> *"Note: You are connected to a self-hosted engine (http://localhost:8000). Projects, team members, and billing are cloud-managed services on remember.dev."*

### 3.2 Data Plane Commands (`https://<tenant>.dp.remember.dev` or `http://localhost:8000`)
Authenticated via tenant data plane tokens (`umc_dp_...` in cloud) or pre-shared bearer secrets (`API_BEARER_BIND` in self-hosted mode):
- **`remember setup [OPTIONS]`**: The Sentry-like AI harness bootstrapper (see §4).
- **`remember ingest <path> [OPTIONS]`**: Streams markdown or source files through the E0 ingestion endpoint.
- **`remember query <text> [OPTIONS]`**: Executes assured context retrieval (`facts_context` or `combined_context`) and prints formatted JSON or markdown summaries.
- **`remember operations list|run`**: Discovers and invokes remote assured operations by name.
- **`remember mcp [OPTIONS]`**: Runs the Model Context Protocol (MCP) server over `stdio` (or Streamable HTTP) forwarding requests to the resolved data plane.
- **`remember doctor`**: Verifies connectivity, auth token validity, tenant endpoint health, and agent harness configurations.

### 3.3 Retirement of Legacy Commands
- **`remember review` (Retired)**: The D24 human review queue (`review list`, `review decide`) was built for early prototype cluster curation. The production engine uses autonomous bitemporal adjudication (D3/D43/D107). Human review has been removed from all documentation and is not part of the public product.
- **`remember budget` (Retired from Client)**: Spend ceiling inspection on local database ledgers is an internal worker detail, replaced on the platform level by `remember balance`.
- **`remember ops` (Confined to Internal/Docker)**: SRE tasks (`ops replay`, `ops rebuild`) are executed inside the server container environment via internal scripts, not exposed on the developer client CLI.

### 3.4 Credential Architecture & Token Isolation (Amending D92)
D92 originally modeled `remember login` as a flat client storing a single deployment-bound token. D108 amends D92 to establish structured, multi-tenant credential storage with strict audience isolation:

```json
{
  "version": 1,
  "control_plane": {
    "url": "https://api.remember.dev",
    "access_token": "umc_usr_...",
    "org_id": "0191...",
    "user_id": "0191...",
    "email": "dev@example.com"
  },
  "active_project_id": "0191-proj-alpha",
  "projects": {
    "0191-proj-alpha": {
      "name": "production",
      "data_plane_url": "https://tenant-alpha.dp.remember.dev",
      "data_plane_token": "umc_dp_..."
    }
  }
}
```

1. **Strict Transport & Audience Isolation**:
   - Control-plane credentials (`control_plane.access_token`) are used **exclusively** against `https://api.remember.dev` and are **never** forwarded to tenant data planes.
   - Tenant data-plane tokens (`umc_dp_...`) are presented **only** to the tenant's data plane (`https://<tenant>.dp.remember.dev`).
2. **Resolution Precedence for Data Plane Operations**:
   - Explicit CLI flag: `--token <token>` and `--url <url>`.
   - Explicit environment: `REMEMBER_TOKEN` and `REMEMBER_DATA_PLANE_URL`.
   - Ambient stored project: `projects[active_project_id]`.
   - Fallback self-hosted: `http://localhost:8000` with `REMEMBER_TOKEN`.
3. **Preshared Secret Parity for Self-Hosted Engines**:
   - Self-hosted engines authenticate via `HashedBearerAuth` matching the SHA-256 digest of the presented bearer secret against `API_BEARER_BIND`.
   - Possessing this bearer secret grants full authority over the instance.
   - Consequently, self-hosted tokens are treated with the **exact same confidentiality and secret-isolation protections** (D92/D108) as cloud tokens: zero git commits, `0600` disk permissions, and process-isolated environment variables.

---

## 4. Coding Agent Bootstrapper (`remember setup`)

### 4.1 Invocation Grammar
```bash
remember setup [OPTIONS]

Options:
  --cloud                          Configure for Managed Cloud (default)
  --self-hosted                    Configure for local self-hosted engine (http://localhost:8000)
  --url TEXT                       Explicit data-plane URL override
  --token TEXT                     Explicit token override (discouraged; ambient login preferred)
  --agent [cursor|claude|codex|agy|all]  Target specific harness (auto-detects all present if omitted)
  --dry-run                        Preview file modifications without writing
  --help                           Show this message and exit
```

### 4.2 Credential Safety & Invariants (D92 / D108)
1. **Zero Secret Leakage in Git**: Project-local configuration files (`.cursor/mcp.json`, `.agents/mcp_config.json`, `.codex/config.toml`) must **never** embed plaintext API tokens.
2. **Ambient Credential Resolution**:
   - The CLI executable resolves credentials dynamically at runtime via the precedence cascade in §3.4.
   - Injected harness configuration delegates entirely to the local CLI binary or references standard environment variable interpolation (`${env:REMEMBER_TOKEN}`).

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
   - Release `remember 0.4.0` owning the `remember` CLI binary.
   - Release a final `rememberstack` update (e.g. `0.16.1` or `1.0.0`) containing a clear terminal deprecation warning directing users to `remember` for client usage and Docker Compose for server deployments.
3. **Phase 3: Docs & Quickstart Updates**:
   - Update `remember.dev/docs` and `docs.remember.dev` quickstart guides to feature `uvx remember setup` as the primary onboarding command.
   - Deprecate bare-metal pip install instructions for self-hosters; standardize all self-hosted guides on Docker Compose.

---

## 7. Consequences & Preserved Invariants

- **D92 & D108 (Secret Isolation)**: API tokens and bearer secrets (both cloud and self-hosted) are never written into committed git repositories or project-local files.
- **D43 (Autonomous Bitemporal Memory)**: All recall and truth adjudication operates autonomously without blocking on human queues.
- **D66 (Honest Status & Balance)**: Balance and credit transparency is maintained across both web UI and CLI (`remember balance`).
- **D92 (CLI Credential Storage)**: Amended to store structured credentials with strict separation between the control-plane user session and per-project data-plane tokens.
- **Durable Zero-Friction Onboarding**: `uvx remember setup` automatically configures persistent, absolute-path launchers (`<resolved_launcher_path> [remember] mcp`) that operate seamlessly without manual `$PATH` intervention or GUI editor `FileNotFoundError` failures.
- **Zero Host-Dependency Friction**: Developers and AI agents never encounter C-extension compilation errors when adopting Remember.
