# Unified `remember` Distribution, Container-First Engine, and Platform CLI (Design)

> **Binding D108 decision (2026-09-05).** The single canonical package on PyPI is **`remember`**.
> The database and worker engine is distributed exclusively via Docker container images (`ghcr.io/writeitai/remember-stack:<tag>`)
> and Docker Compose. `rememberstack` is retired from standalone PyPI distribution. The binary command
> **`remember`** is owned exclusively by the `remember` package. Legacy human-review queue commands (`review`)
> and spend-ceiling inspection (`budget`) are retired from public client surfaces; autonomous bitemporal
> adjudication (D3/D43/D107) is the sole engine truth authority. The `remember` CLI operates as a full platform
> tool supporting both Cloud control-plane management (`login`, `whoami`, `balance`, `projects`, `members`) and data-plane
> operations (`setup`, `ingest`, `query`, `mcp`), defaulting to Managed Cloud (`remember.dev`) with `--self-hosted` for local engines.

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
│  - Dependencies: `httpx>=0.28.1`, `pydantic>=2.11` (zero server dependencies)│
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
- **`remember login`**: Initiates device-code OAuth flow against `remember.dev`, prompts in terminal or opens browser, writes owner-only `0600` credential file to `~/.config/remember/credentials.json`.
- **`remember logout`**: Idempotently revokes the active token at the control plane and removes stored credentials.
- **`remember whoami`**: Displays authenticated identity, active organization, and current project context.
- **`remember balance`** (or `remember billing`): Fetches current credit balance and subscription status (e.g. `Current balance: €15.00 [Active]`).
- **`remember projects list`**: Enumerates available tenant deployments in the user's organization.
- **`remember projects create <name>`**: Provisions a new tenant data plane via the control-plane API.
- **`remember switch <project>`**: Sets the default active project in local configuration.
- **`remember members list`** / **`remember members invite <email>`**: Manages team organization seats.

*Self-Hosted Behavior*: When configured in self-hosted mode (`--self-hosted`), executing control-plane commands prints a clean, honest notice:
> *"Note: You are connected to a self-hosted engine (http://localhost:8000). Projects, team members, and billing are cloud-managed services on remember.dev."*

### 3.2 Data Plane Commands (`https://<tenant>.dp.remember.dev` or `http://localhost:8000`)
Authenticated via tenant data plane tokens (`umc_dp_...` in cloud) or non-secret bearer tokens (self-hosted):
- **`remember setup [OPTIONS]`**: The Sentry-like AI harness bootstrapper (see §4).
- **`remember ingest <path> [OPTIONS]`**: Streams markdown or source files through the E0 ingestion endpoint.
- **`remember query <text> [OPTIONS]`**: Executes assured context retrieval (`fact_context` or `answer_context`) and prints formatted JSON or markdown summaries.
- **`remember operations list|run`**: Discovers and invokes remote assured operations by name.
- **`remember mcp [OPTIONS]`**: Runs the Model Context Protocol (MCP) server over `stdio` (or Streamable HTTP) forwarding requests to the resolved data plane.
- **`remember doctor`**: Verifies connectivity, auth token validity, tenant endpoint health, and agent harness configurations.

### 3.3 Retirement of Legacy Commands
- **`remember review` (Retired)**: The D24 human review queue (`review list`, `review decide`) was built for early prototype cluster curation. The production engine uses autonomous bitemporal adjudication (D3/D43/D107). Human review has been removed from all documentation and is not part of the public product.
- **`remember budget` (Retired from Client)**: Spend ceiling inspection on local database ledgers is an internal worker detail, replaced on the platform level by `remember balance`.
- **`remember ops` (Confined to Internal/Docker)**: SRE tasks (`ops replay`, `ops rebuild`) are executed inside the server container environment via internal scripts, not exposed on the developer client CLI.

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

### 4.2 Credential Safety & Invariants (D35 / D65)
1. **Zero Secret Leakage in Git**: Project-local configuration files (`.cursor/mcp.json`, `.agents/mcp_config.json`) must **never** embed plaintext API tokens.
2. **Resolution Cascade**:
   - The CLI executable resolves credentials dynamically at runtime from:
     1. Environment variables (`REMEMBER_TOKEN` / `REMEMBER_DATA_PLANE_URL`).
     2. Stored user credentials (`~/.config/remember/credentials.json`, file mode `0600`).
     3. Interactive device login prompt (`remember login`).
   - Injected harness configuration simply invokes the local binary:
     ```json
     {
       "mcpServers": {
         "remember": {
           "command": "remember",
           "args": ["mcp", "--profile", "default"]
         }
       }
     }
     ```
     or references standard environment interpolation (`${env:REMEMBER_TOKEN}`).

### 4.3 Harness Configuration Matrix

| Harness | Detection Trigger | Configuration Action |
| :--- | :--- | :--- |
| **Cursor** | `.cursor/` directory exists | 1. Merges `remember` into `.cursor/mcp.json`<br>2. Writes `.cursor/rules/remember.mdc` |
| **Claude Code** | `claude` CLI on PATH | Executes native `claude mcp add remember -- remember mcp` |
| **Claude Desktop** | `claude_desktop_config.json` exists | Merges `mcpServers.remember` into configuration |
| **Codex** | `.codex/` or `config.toml` exists | Configures `[mcp_servers.remember]` table in `config.toml` |
| **Antigravity** | `.agents/` directory exists | Writes `.agents/skills/remember/SKILL.md` and updates `mcp_config.json` |

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
   - Ensure `pyproject.toml` for `remember` declares only `httpx` and `pydantic`.
2. **Phase 2: PyPI Cutover**:
   - Release `remember 0.4.0` owning the `remember` CLI binary.
   - Release a final `rememberstack` update (e.g. `0.16.1` or `1.0.0`) containing a clear terminal deprecation warning directing users to `remember` for client usage and Docker Compose for server deployments.
3. **Phase 3: Docs & Quickstart Updates**:
   - Update `remember.dev/docs` and `docs.remember.dev` quickstart guides to feature `uvx remember setup` as the primary onboarding command.
   - Deprecate bare-metal pip install instructions for self-hosters; standardize all self-hosted guides on Docker Compose.

---

## 7. Consequences & Preserved Invariants

- **D35 & D65 (Secret Isolation)**: API tokens are never written into committed git repositories or project-local files.
- **D43 (Autonomous Bitemporal Memory)**: All recall and truth adjudication operates autonomously without blocking on human queues.
- **D66 (Honest Status & Balance)**: Balance and credit transparency is maintained across both web UI and CLI (`remember balance`).
- **Zero Host-Dependency Friction**: Developers and AI agents never encounter C-extension compilation errors when adopting Remember.
