# Unified Documentation Architecture (The Qdrant Model) and Full Cloud Query Space Parity (Design)

> **Binding D109 decision (2026-09-06).** The single canonical documentation site lives in the
> `writeitai/remember-stack` repository under `website/` and is served canonically at
> **`https://remember.dev/docs`** (with `docs.remember.dev` 301-redirecting to `remember.dev/docs`).
> The documentation adopts the **Qdrant unified architecture model**, presenting an authoritative
> narrative combining deep conceptual foundations (bitemporal memory, contradiction adjudication, graph retrieval),
> a universal quickstart (`uvx remember setup`), identical client/MCP surfaces (`remember`), and an honest,
> transparent deployment choice (Self-Hosted Docker Compose vs. Managed Cloud). The SQL and bitemporal
> graph query space (`open_query_execute`) is **fully implemented and active on Self-Hosted Engine (v0.17.0+)**,
> with complete local-to-cloud parity planned for Remember Cloud under active operator dogfooding, backed
> by dedicated per-project database pods and the AST-validated query sandbox (`QuerySandboxExecutor`).
> Duplicate documentation pages in `ultimate-memory-cloud` are retired.


---

## 1. Context & Problem Statement

### 1.1 Documentation Fragmentation & Dual-Site Confusion
Prior to D109, documentation was divided across two repositories and two domains:
1. **`writeitai/remember-stack/website`**: An in-repo Next.js MDX documentation site conceived under D66, deployed to `docs.remember.dev`.
2. **`writeitai/ultimate-memory-cloud/fe/src/app/(public)/docs`**: A separate documentation subset conceived during cloud portal development, deployed to `remember.dev/docs`.

This split introduced severe operational and product friction:
- **Maintenance Drift**: An engineer changing an engine feature, an MCP descriptor, or a Python SDK method in `remember-stack` had to manually mirror changes across to `ultimate-memory-cloud`, inevitably causing documentation divergence within weeks.
- **Brand & Consumer Confusion**: Users and AI coding agents were presented with two different documentation sites with conflicting navigation, different API route listings, and fragmented instructions.
- **Cannibalized SEO & Machine Discovery**: Search engines and AI crawler tools (`llms.txt`, `llms-full.txt`) indexed fragmented subsets of the platform.

### 1.2 The Artificial Cloud SQL Prohibition
In early cloud planning (`managed-compat-2026-09.yaml`), `open_query_execute` (`POST /query/sql`, `GET /query/space`, and the associated open-query MCP tools) was marked as `unsupported` on Remember Cloud.

This restriction was an artifact of conservative initial scoping, but it created significant drawbacks:
- **Data Plane Divergence**: An AI coding agent developed against a local Docker container had 7 powerful analytical and graph query tools (`facts_current`, graph exploration, bitemporal SQL), but when pointed to Remember Cloud, those exact tools failed with HTTP 404.
- **Suppression of Core Value**: Bitemporal, relational graph querying over attested facts is Remember's defining technical advantage over standard top-K vector databases. Disabling it in the managed cloud offering crippled our best feature for paying customers.
- **Redundant Scoping**: In Remember Cloud, every customer project runs in its own private container pod with a **dedicated PostgreSQL database instance** (physical pod isolation, zero shared multi-tenant tables). The engine already enforces deny-by-default AST parsing (`pglast`), `READ ONLY` transaction blocks, view whitelisting (`memory_v1`), and execution timeouts. There is zero architectural reason to disable it.

---

## 2. The Qdrant Model for Developer Infrastructure

Industry-leading open-core infrastructure companies (**Qdrant**, **Supabase**, **PostHog**, **Temporal**) treat documentation as their **primary product marketing, onboarding, and conversion asset**.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Single Authoritative Surface: `https://remember.dev/docs`                  │
│                                                                              │
│  - Stored in: `writeitai/remember-stack` (`website/`)                        │
│  - Enforces: Decision D66 Same-PR Truthfulness Contract                      │
│  - Machine Index: `https://remember.dev/llms.txt`                            │
└──────────────────────────────────────────────────────────────────────────────┘
                                       │
        ┌──────────────────────────────┴──────────────────────────────┐
        ▼                                                             ▼
┌──────────────────────────────┐              ┌──────────────────────────────┐
│  Conceptual & Client Layer   │              │  Deployment Choice Layer     │
│  (100% Identical Everywhere) │              │  (Transparent & Honest)      │
│                              │              │                              │
│  - Bitemporal Memory Theory  │              │  [Self-Hosted OSS]           │
│  - E0–E3 Pipeline Architecture│             │  - Docker Compose            │
│  - Universal `remember setup`│              │  - Kubernetes / Helm         │
│  - Python SDK (`remember`)   │              │  - Bare PostgreSQL 19 + MinIO│
│  - Dynamic MCP Server        │              │                              │
│  - Assured Context Retrieval │              │  [Managed Cloud]             │
│  - Open SQL & Graph Space    │              │  - Dedicated Private Pods    │
│                              │              │  - Automated Compaction      │
│                              │              │  - Zero-Ops Backups & Alerts │
│                              │              │  - Platform CLI (`balance`)  │
└──────────────────────────────┘              └──────────────────────────────┘
```

### 2.1 Core Pillars of the Qdrant Model
1. **One Unified Narrative**: The platform explains the underlying data and AI primitives first (how bitemporal memory works, why semantic vectors alone fail, how contradiction resolution operates). This establishes technical authority and educates both human engineers and AI coding agents.
2. **One Universal Quickstart**: The onboarding guide starts with `uvx remember setup`. The user chooses whether to connect to Managed Cloud (instant, free trial credits) or Self-Hosted Docker Compose (`http://localhost:8000`).
3. **Identical Code & MCP Tools**: The Python SDK (`RememberClient`) and MCP adapter (`remember mcp`) run identically regardless of backend destination. Zero code rewriting when moving from development to production.
4. **Transparent Cloud Value Add**: The docs celebrate open source while clearly articulating why developers choose Cloud: zero-ops database management, automatic daily bitemporal compaction, automatic backups, managed OAuth device-grant credentials, and team seats.

---

## 3. Unified Documentation Taxonomy

The site navigation on `https://remember.dev/docs` is organized into five focused sections:

### 3.1 Section 1: Overview & Architecture (The "Why")
- **What is Remember?**: The open memory infrastructure for autonomous AI agents.
- **Why Vector RAG Fails for Memory**: Ephemeral context windows, semantic drift, stale facts, inability to resolve contradictions, lack of bitemporal truth vs. belief timelines.
- **Engine Architecture (E0–E3 Pipeline)**:
  - **E0**: Document ingestion, multi-modal conversion, and PageIndex structural trees.
  - **E1**: Chunks and atomic claim extraction.
  - **E2**: Bitemporal reconciliation and autonomous contradiction adjudication.
  - **E3**: Knowledge graph projection, relation labeling, and entity nomination.
- **Trust, Provenance & Auditability**: Content-addressed SHA-256 storage, strict source locators, and deterministic audit ledgers.

### 3.2 Section 2: Universal Quickstart (Zero-to-Agent in 2 Minutes)
- **One-Command Setup**: `uvx remember setup` (interactive prompts for Cloud vs. Self-Hosted).
- **Harness Integrations**: Dedicated setup tabs for Cursor, Claude Code, Claude Desktop, Codex, and Antigravity.
- **Instant Verification**: Running `remember doctor` to verify tenant connectivity and MCP registration.

### 3.3 Section 3: Core Capabilities & Client SDK (Unified Data Plane)
- **Python SDK Reference**: Complete guide to `from remember import RememberClient`, with examples for ingestion, fact search, and assured operations.
- **Platform CLI Reference**: Comprehensive manual for `remember ingest`, `remember query`, and `remember operations`.
- **Model Context Protocol (MCP)**: Tool catalog, stdio integration, and how agents call memory tools.
- **Assured Retrieval Operations**: Contract specifications for `fact_context` and `answer_context`.
- **Open Query Space (`open_query`)**: Full guide to querying the bitemporal relational graph schema (`memory_v1` views: `facts_current`, `graph_edges_current`, `contradiction_members_current`) via SQL.

### 3.4 Section 4: Self-Hosting & Operations (Open Source)
- **Docker Compose Quickstart**: Complete guide to launching the engine, PostgreSQL 19 with SQL/PGQ and `pgvector`, and MinIO storage locally.
- **Production Kubernetes / Helm**: Cluster deployment blueprints, persistent volume configurations, worker scaling (E0–E3), and environment variables.
- **Database Architecture**: PostgreSQL 19 schema spine, SQL/PGQ graph queries, Lance/pgvector index maintenance, and Alembic migrations.
- **Backup, Restore & Disaster Recovery**: Cold storage snapshots, S3-compatible replication, and point-in-time recovery.

### 3.5 Section 5: Remember Cloud Platform (Managed Services)
- **Cloud Architecture & Physical Isolation**: How each project runs inside an isolated, dedicated container and database instance.
- **Control Plane CLI**: `remember login`, `remember logout`, `remember whoami`, `remember projects`, `remember switch`, and `remember members`.
- **Billing, Usage & Credit Balance**: Credit model explanation, monitoring spend via `remember balance`, and payment methods.
- **Security, Compliance & Data Sovereignty**: EU data residency, GDPR compliance, encryption at rest and in transit, and secret isolation (D92/D108).

---

## 4. Full Cloud Query Space Parity (`open_query`)

### 4.1 Architecture & Tenant Isolation Guarantees
The open query space is active on the Self-Hosted Engine (v0.17.0+) and planned for Remember Cloud tenant deployments following operator dogfooding. The security model relies on physical pod isolation and three layers of query sandbox defense:

1. **Physical Database & Pod Isolation**:
   - Remember Cloud allocates a **private, dedicated PostgreSQL instance** for every project.
   - Cross-tenant data leakage via SQL is physically impossible because no two tenants share database tables, schemas, or memory buffers.
2. **Deny-by-Default AST Parsing (`pglast`)**:
   - Raw SQL queries never touch PostgreSQL unexamined.
   - The query AST is parsed using PostgreSQL's own grammar (`pglast 8.x`).
   - Mutations (`INSERT`, `UPDATE`, `DELETE`), DDL (`DROP`, `CREATE`, `ALTER`), administrative commands (`VACUUM`, `SET`, `GRANT`), and multi-statement queries are unconditionally rejected before execution.
3. **Authoritative `memory_v1` Schema Contract**:
   - Queries are strictly validated against the authoritative `memory_v1` public surface defined in `plan/designs/open_query_space_design.md` §3.3 & §3.4 and declared in `src/rememberstack/spine/query_space/memory_v1_manifest.json`:
     - **24 Public Relations (Views)**: `changes_visible`, `chunks_live`, `claim_occurrences_live`, `claims_live`, `claims_visible_history`, `contradiction_members_current`, `document_crossrefs_live`, `document_versions_visible`, `documents_live`, `entities_current`, `entity_aliases_current`, `entity_document_mentions`, `evidence_lineage`, `fact_claim_evidence_live`, `facts_current`, `facts_visible_history`, `graph_edges_current`, `graph_edges_visible_history`, `identity_events_visible`, `mentions_live`, `page_evidence_visible`, `pages_live`, `sections_live`, `testimony_currency_events_visible`.
     - **11 Allowlisted SQL-Callable Functions**: `facts_as_of(valid_at, believed_at, max_rows)` (the bitemporal point-in-time facts SRF), graph traversal helpers (`graph_neighborhood`, `graph_path`, `graph_citation_path`), lexical/body helpers (`lexical_chunks`, `lexical_claims`, `fetch_chunk_bodies`), and projection-backed calls (`semantic_chunks`, `semantic_claims`, `semantic_entities`, `semantic_facts`).
   - Direct access to internal engine spine tables (`spine_facts`, `p1_lance_*`, `worker_leases`), non-public base evidence tables, or system catalogs is blocked at parse time.
   - 100% data-plane parity: Cloud tenants have access to the exact same 24 relations and 11 allowlisted functions as Self-Hosted deployments.
4. **Sandboxed Execution Runtime (`QuerySandboxExecutor`)**:
   - Queries run inside explicit `READ ONLY` transaction blocks under a sandboxed, low-privilege role.
   - Hard clamps are applied via `SET LOCAL` session variables:
     - `statement_timeout = clamp_timeout_ms` (interactive default: 5,000ms).
     - Row count capped at `clamp_rows` (default: 100 rows).
     - Byte payload capped at `clamp_bytes` (default: 1 MB).
5. **Spend Safety, Request Admission & Usage Metering**:
   - Request admission and spend protection are enforced at two levels:
     - **Engine Level (Spend Lease Port)**: Update `_spend_gated_route` in `src/rememberstack/surfaces/http_api.py` to gate `/query/sql`, `/query/sql/explain`, and `/query/space`, reserving spend under `path_id="search"` (or dedicated `"open_query"`) with D46 spend lease reservation on entry and commit on 2xx response.
     - **Cloud Gateway Level**: Proxy ingress validates tenant token balance and active project status before routing execution to the project's dedicated data plane pod.
   - Rate limiting and query timeouts (5,000ms default) prevent runaway agent query loops or accidental high-resource scans.

### 4.2 Updated Compatibility Matrix and Rollout Posture
Open query surfaces are active and fully supported on Self-Hosted Engine v0.17.0+. On Remember Cloud, these routes are planned following operator dogfooding and will transition to supported upon deployment of v0.17.0+ data plane pods:

| Surface / Route | Previous Cloud Status | Self-Hosted v0.17.0+ Status | D109 Cloud Target Status | Rationale |
| :--- | :--- | :--- | :--- | :--- |
| `POST /query/sql` | `unsupported` | **`supported`** | **`planned`** | AST-validated, read-only sandboxed SQL execution. |
| `POST /query/sql/explain` | `unsupported` | **`supported`** | **`planned`** | Sandboxed execution plan inspection. |
| `GET /query/space` | `unsupported` | **`supported`** | **`planned`** | Dynamic manifest-backed schema discovery. |
| `GET /query/space/search` | `unsupported` | **`supported`** | **`planned`** | Semantic and lexical search over schema manifest text. |
| `open_query_execute` (SDK) | `unsupported` | **`supported`** | **`planned`** | Enables `RememberClient.open_query` in Python. |
| Open Query MCP Tools | Omitted | **`advertised`** | **`planned`** | The 7 open-query tools appear in `remember mcp`. |


---

## 5. Physical Storage, Routing, and SEO Topology

### 5.1 Repository Ownership & Same-PR Truthfulness (D66)
- The documentation source code and MDX files are housed exclusively in **`writeitai/remember-stack`** under the **`website/`** directory.
- Any engine pull request that modifies an API route, SDK method, MCP descriptor, or configuration flag must update the corresponding MDX documentation in `website/` within the same PR.
- CI in `remember-stack` runs quality, linting, and link-integrity checks (`pnpm build`, `markdownlint`) on `website/` to enforce correctness.

### 5.2 Routing & Canonical Domain
- **Canonical Address**: `https://remember.dev/docs` is the primary public entry point.
- **Subdomain Redirects (301 Permanent)**:
  - Root: `https://docs.remember.dev/` → `https://remember.dev/docs`
  - Existing paths already prefixed with `/docs/`: `https://docs.remember.dev/docs/:path*` → `https://remember.dev/docs/:path*` (avoids duplicate `/docs/docs/...`).
  - Legacy bare paths: `https://docs.remember.dev/:path*` (where `:path` is not `docs/*`, `llms.txt`, etc.) → `https://remember.dev/docs/:path*`.
  - Machine discovery: `https://docs.remember.dev/llms.txt` → `https://remember.dev/llms.txt` and `https://docs.remember.dev/llms-full.txt` → `https://remember.dev/llms-full.txt`.
- **Cloud Gateway Routing**: The Cloud web router at `remember.dev` forwards requests matching `/docs*` to the Next.js documentation service built from `remember-stack/website`.
- **Canonical Link Tags & Machine Discovery**: All documentation pages emit `<link rel="canonical" href="https://remember.dev/docs/..." />` to consolidate search authority. `https://remember.dev/llms.txt` and `https://remember.dev/llms-full.txt` are served directly from the canonical docs asset manifest.

### 5.3 Deprecation of Duplicate Docs in `ultimate-memory-cloud`
- The static documentation pages in `ultimate-memory-cloud/fe/src/app/(public)/docs` are retired.
- In-app links in the Cloud dashboard (e.g. `/app/onboarding`, `/app/settings`) link directly to `https://remember.dev/docs/...`.

---

## 6. Migration Plan

1. **Phase 1: Merge D109 Design & Decision**:
   - Merge this design and D109 into `writeitai/remember-stack`.
2. **Phase 2: Engine Spend Gating & Cloud Parity Enablement**:
   - **Engine Route Spend Gating**: Update `_spend_gated_route` in `src/rememberstack/surfaces/http_api.py` to register `/query/sql`, `/query/sql/explain`, and `/query/space` under `path_id="search"` (or `"open_query"`). Add integration tests verifying spend reservation, 2xx commit, and non-2xx lease release under `SpendLeasePort`.
   - **Cloud Gateway & Compatibility Manifests**: Update `ultimate-memory-cloud` compatibility matrices (`docs/compatibility/managed-compat-2026-09.yaml` and `.md`) marking `open_query_execute` and `/query/*` as supported.
   - **Deployment**: Deploy engine v0.16.0+ with `open_query` composed on tenant data-plane pods with verified spend metering and AST sandbox execution.
3. **Phase 3: Docs Consolidation & Narrative Polish**:
   - Reorganize `website/` in `remember-stack` according to the 5-layer Qdrant taxonomy.
   - Point Cloud web ingress for `remember.dev/docs` to the canonical docs build.
   - Issue 301 redirects from `docs.remember.dev`.

---

## 7. Consequences & Preserved Invariants

- **D66 (Public Documentation Site)**: Strongly reinforced and expanded. The in-repo documentation site at `website/` is now the sole authority for both OSS and Cloud.
- **D92 & D108 (Secret Isolation & Single Package)**: All documentation examples feature `uvx remember setup` and `remember` CLI commands, maintaining absolute-path launcher emission and zero credential leakage in git.
- **100% Data-Plane Parity**: Developers and agents experience identical behavior, endpoints, and MCP toolsets across self-hosted and cloud environments.
- **Zero Host Compilation Burden**: Documentation emphasizes Docker Compose for self-hosters and `pip install remember` for application developers.
