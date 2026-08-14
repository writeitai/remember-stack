# REVIEW — Claude claude-opus-5 xhigh — request-path metering design

**Verdict:** REQUEST_CHANGES

I've read the analysis, the design, and every cited source (plus the catalog contract, the OpenRouter adapter, the query-space grants, and the docs IA). Full review below.

---

# Dual review — Request-path metering, content-free cost export, device-grant login

**Scope reviewed:** `plan/analysis/request_path_metering_and_cost_export_analysis.md`, `plan/designs/request_path_metering_and_cost_export_design.md`, against `main` at `1576fed3`.

## 1. Verdict

**REQUEST_CHANGES.**

The core architectural judgment is right and I would not relitigate it: `cost_ledger`'s correctness comes from D67's lock-and-copy attribution off a *running* `processing_state` row (`work_ledger.py:782-828`, `_SELECT_FOR_COST` at `:1564`), and a search genuinely is not such a row. Alternatives A and B are correctly rejected for the correct reasons. Pull-over-push is correct. A separate export credential is correct.

What fails review is the part the design treats as mechanical: **the union is not actually a sound read model**. The cursor loses rows, the money column rounds interactive spend to zero, billed-but-failed calls are dropped, a uniqueness constraint converts real spend into silence, the loss path is invisible to the consumer it delegates to, and the one composition detail that makes the auth claim true is stated backwards against how `http_api.py` is built. Several of these individually defeat the design's own stated purpose ("heavy retrieval … can be most of the bill and still look free").

---

## 2. Blockers

### B1 — `numeric(12,6)` rounds most interactive embed spend to `$0.000000`
**Where:** design §3 DDL (`cost_usd numeric(12,6)`), copied from `cost_ledger` (`p0_02_0002_infrastructure_registries.py:154`).

**Invariant violated:** the design's own §1.3 / analysis §2 ("`cost_ledger` systematically under-reports; heavy retrieval against a small corpus can be most of the bill and still look free").

Worker rows are LLM calls costing cents — 6 decimal places is fine. A request-path row is a single ~10-token query embed. At `text-embedding-3-small` pricing (~$0.02/1M tokens) that is ~$0.0000002, which Postgres rounds to **`0.000000`**. At `text-embedding-3-large` (~$0.13/1M) it is ~$0.0000013 → `0.000001`, a ~23% error. `ProviderCallUsage.cost_usd` is a `Decimal` (`model/model_provider.py:37`) and OpenRouter's `cost` is parsed at full precision (`adapters/openrouter.py:693`) — the precision is thrown away at the INSERT this design adds. The table built to stop under-reporting would report zero for the exact workload it exists to measure.

**Fix:** pick a scale from measured per-call embed cost (e.g. `numeric(20,12)`), and state the union/rounding policy in the export when the two tables have different scales.

### B2 — the cursor is not gap-free; rows commit after their own timestamp
**Where:** design §5.1 (`cursor`/`next_cursor` encode `(occurred_at, source, cost_id)`), analysis §6 ("Replay of the same cursor returns the same rows").

**Invariant violated:** §5.4's consumer contract ("do not invent `cost_usd` when pages stop arriving") assumes the only failure is *silence*. This failure is worse: pages keep arriving and rows are permanently skipped.

`occurred_at` defaults to `now()`, which in Postgres is **transaction start time**, not commit time. `record_call` opens a transaction, takes `SELECT … FOR UPDATE` on `processing_state` (`work_ledger.py:790-806`), and commits later — under lock contention the gap is unbounded. A consumer that advanced its watermark to `T` will never see a row stamped `T-δ` that commits at `T+ε`. Ordering by a wall-clock default is the classic lost-row cursor.

This also collides with the design's own scope claim: a gap-free cursor needs a **monotonic export sequence** (or commit-order key) on *both* tables, i.e. a change to `cost_ledger` — which §1.1 and the `Amends:` header say is untouched. "Identity unchanged" and "no new columns/indexes" are different statements; the design must say which one it means.

**Fix:** specify a `bigserial`/sequence export key (or a documented safety-lag window with an explicit completeness argument), on both ledgers, and amend the `Amends:` header accordingly.

### B3 — the export route cannot be exempted from the customer perimeter the way §11 says
**Where:** design §11 ("Preferred: mount `/ops/cost-export` on the same process with its own dependency, never on the list that gates `/search/*`") vs `surfaces/http_api.py:213-226`.

**Invariant violated:** design §5.2 ("A valid customer perimeter token is `401` here") and §1.5.

`build_api` passes `dependencies=[perimeter, admission]` into `FastAPI(...)`. FastAPI stores those on the app's router and prepends them to **every** `APIRoute` added to that app, including routes registered after construction. There is no per-route opt-out. So the "preferred" option is not implementable: adding `@app.get("/ops/cost-export")` inherits `_perimeter` (`:708-741`) and would 401 the ops credential before the export check ever runs. Only the alternative the design mentions second — a **separately composed ASGI app mounted** by the profile — actually bypasses router-level dependencies. The design names the working option as the fallback and the broken option as preferred.

Second, unstated consequence: mounting on the same app also inherits `_admission` (`:744-756`), so during a D74 hard forget the export returns `503` — the heartbeat goes dark precisely while spend continues, contradicting §5.2's "Silence … is not zero spend." The design must state explicitly whether export is subject to D74 admission (it should not be) and why that is safe (it reads no content).

**Fix:** make the separately-composed-app the binding composition; state the D74 exemption; add the test §8 is missing (customer token → 401 *and* forget-in-progress → still 200).

### B4 — billed-but-failed provider calls are silently dropped
**Where:** design §1.3 ("**Every** provider call the API process makes is recorded") vs §4.1 ("records one **successful** provider response").

**Invariant violated:** the design contradicts itself, and drops money the engine already knows about.

`adapters/openrouter.py:475-486` raises `OpenRouterInvalidResponseError(..., usage=usage)` — a call that **billed** and then failed validation. `ProviderCallError` carries usage precisely so it is not lost (`model/model_provider.py:41-47`), and the worker path treats it as first-class (`workers/base.py:263`). The request path as designed records nothing for it. That is systematic under-reporting concentrated in exactly the degraded conditions where spend spikes.

Related, and also unaddressed: `_usage` raises `ProviderAccountingError` when the provider returns no usage object (`adapters/openrouter.py:676-687`). On the request path that exception propagates out of `QueryEngine._embed` (`query_engine.py:2666-2671`) and 500s the user's search — which contradicts §4.3's principle that metering never fails the user-visible query. The design owes a decision here.

### B5 — `UNIQUE (deployment_id, request_id, call_key)` converts real spend into silent loss
**Where:** design §3 DDL + rule 1 ("A retried embed with the same key does not double-count").

**Invariant violated:** §3's own "the engine must not drop them or the 'what did I spend?' question becomes incomplete."

The worker constraint is meaningful because a claimed unit of work *can* be redelivered and re-run at the same `(processing_id, attempt)` — dedup defends a real replay path (D67, `_INSERT_COST … ON CONFLICT DO NOTHING`, `work_ledger.py:1585`). The request path has **no redelivery**: no inbound HTTP request is replayed under the same minted `request_id`. So the constraint can never prevent a double-bill; it can only *swallow a distinct second call* whose key collided — two concurrent embeds in one operation, a provider-level retry that billed twice, or a counter race. Deduplication-by-collision is undetectable: the insert reports "already recorded" (§7) and returns vectors.

**Fix:** drop the unique constraint (PK `cost_id` is enough for an append-only receipt log), or keep it only with a documented, atomic, provably-unique `call_key` derivation and a distinct log line when a conflict actually fires.

### B6 — the "no content in logs" claim is false as the engine ships
**Where:** design §1.3 ("Query text, vectors, prompts, and retrieved memory never enter the row, the export payload, **or application logs of those paths**").

**Invariant violated:** Rule 1 (a cold reader will believe this) and the design's own content-free premise.

`GET /search/claims?query=…` and `GET /search/chunks?query=…` take the user query as a **URL query parameter** (`http_api.py:279-299`), and self-host runs `uvicorn.run(..., access_log=True)` (`profiles/selfhost.py:824-830`), whose default access-log line contains the full request target including the query string. Every semantic search already writes the user's query text to the application log of that path.

**Fix:** either narrow the claim to "the ledger row and the export payload" (honest, and still worth stating), or make access-log redaction part of this design. Do not ship the sentence as written.

### B7 — Rule 2: interactive ceilings and retention are deferred, not decided
**Where:** design §3 ("Interactive ceilings, if an operator wants them, are a separate policy") and the absence of any retention/partitioning section.

**Invariant violated:** CLAUDE.md Rule 2 — full scope, no deferral; scale is a requirement.

Two holes:
1. D67 exists because unbounded provider spend is unacceptable. This design adds a second, *uncapped* spend path and hands the ceiling question off with "if an operator wants them." That is a deferral, not a simplification and not a stated non-goal. Either design the interactive ceiling (with its fail-open/fail-closed semantics — refusing a search on budget is a retrieval-contract decision) or state it as a **documented non-goal with the reason** the engine deliberately does not cap interactive spend.
2. `surface_cost_ledger` gets a row per provider call per request. At the declared scale it becomes the highest-row-count table in the spine, and the design has no partitioning, retention, or GC story — while the repo already partitions comparable ledgers (`testimony_currency_events`, "partitioned ledger + reconciliation idempotency key", `postgres_schema_design.md` §16 D54). Retention also directly contradicts §5's "replay of the same cursor returns the same rows" once pruning starts.

### B8 — the versioned contract has no versioning mechanism
**Where:** design §5.1 ("Unknown future producer fields require a new contract version (`v2`), not silent addition to `v1`").

`v2` is named and nothing about it is designed: same path or a new one, served simultaneously or not, how a `v1` consumer pins, what deprecation looks like. For a document whose §1.4 headline is "the published read model," that is the missing half. Under Rule 2 this is full-scope content, not sequencing.

Separately, §8's test ("response models reject extra keys") tests the wrong property: `extra="forbid"` stops *undeclared* keys at construction, but the actual risk is a developer **declaring** a new field on the `v1` model. The guard has to be a checked-in assertion over the exact field set (and ideally a golden payload), not `extra="forbid"`.

### B9 — login: contradictory flag grammar, and token-host derivation is a wrong-host footgun
**Where:** design §6.1 vs §6.3.

1. §6.1 declares `remember login [--api-url URL] [--verification-url-base URL]`; §6.3's precedence list resolves `--token-host`. Two names for one concept in a binding contract — two implementers build two CLIs.
2. §6.3 rule 4 derives the device-grant host from `api_url` by stripping a trailing `/dp/v1` or `/dp`, "otherwise the API origin." That is (a) a **UMC-specific URL layout hardcoded into engine defaults**, which is exactly the Rule 3 pressure D60/D61 exist to resist — the library's behavior now encodes one commercial control plane's routing; and (b) a real footgun: `remember login --api-url https://memory.internal.acme` silently POSTs `/v1/device/authorize` to that origin and prints whatever `verification_uri` it answers with, sending the human's browser and the subsequent `device_code` polling to a host that was never nominated as an identity provider.

**Fix:** one flag name; require the token host explicitly (flag/env/file) and fail with a clear message when absent, rather than deriving it from an unrelated URL.

### B10 — the SDK must not pick up an ambient credential file
**Where:** design §6.3 precedence item 3 ("Credential file `access_token` / `api_url`") applied to "`MemoryClient` / remote CLI / remote MCP."

**Invariant violated:** Rule 3 — the library must not quietly become a client of a commercial control plane.

`ClientSettings` (`surfaces/sdk.py:73-80`) is what an embedded `MemoryClient()` resolves from today: env + a documented `http://127.0.0.1:8000` default. Adding a `~/.config/rememberstack/credentials.json` fallback means any process on a developer's machine that constructs a default `MemoryClient()` silently acquires a human's cloud deployment token **and** an `api_url` pointing at a remote host — an embedded library sending traffic somewhere the caller never configured. File resolution belongs to the `remember` CLI entry point (or behind an explicit `MemoryClient.from_config()` / opt-in flag), not to `ClientSettings`.

### B11 — meter loss is invisible to the consumer the design delegates it to
**Where:** design §4.3 ("logged and does not fail the query… supervisors that require completeness treat a gap as their own residual") + §5.4.

The "don't fail the user's search" call is right. But the export gives a consumer **no way to observe the gap**: dropped receipts simply don't exist, `next_cursor` advances normally, and an empty-ish period is indistinguishable from cheap traffic. `surface_cost_record_failed` in a log file is not part of the contract and not reachable by a remote consumer. As written, §5.4's obligation is unsatisfiable, and "honest ledger" degrades to "honest unless Postgres hiccuped, in which case indistinguishable from free."

**Fix:** a durable, content-free loss signal in the contract — e.g. a monotonically increasing per-deployment `dropped_receipts` counter in the page envelope (a tiny table the recorder bumps on failure), or a local durable buffer the recorder retries. Either is content-free and cheap; the current design has neither.

### B12 — amendment surface is materially under-enumerated (design will fail the catalog contract as specified)
**Where:** design §11 ("Catalog | `EXPECTED_TABLES` + enum list") and §9 (docs).

`spine/catalog_contract.py` asserts far more than two lists. Adding a table + enum requires:
- `EXPECTED_ENUMS` (`:60-100`) — `surface_cost_kind`
- `EXPECTED_TABLES` (`:101-171`)
- `EXPECTED_INDEXES` (`:172+`) — `ix_surface_cost_export`
- `EXPECTED_CONSTRAINT_COUNTS` (`:334`) — an **exact** dict (`p`, `u`, `f` all move)
- per-table primary key check (`:523-537`)
- `COMMENT ON TABLE` (count must equal `len(EXPECTED_TABLES)`, `:563-576`) — the design supplies this ✓
- `DECISION_OBJECTS` (`:335-356`) — a `"D91"` entry, mirroring `"D67": ("processing_state", "cost_ledger", …)`

Also missing from `Amends:`:
- `plan/designs/postgres_schema_design.md` **§16 "Decision → table map"** (`:2644`, `:2676`) needs a D91 row, and the D67 row should be narrowed to worker spend.
- **`decisions.md` D67 itself.** D67 currently reads as *the* provider-spend record. After this change it governs worker spend only. See §5 below on D91 vs D67.
- Docs (D66/CLAUDE.md): §9 names only `configuration/page.mdx` and "the CLI getting-started page." The real set is `reference/api/page.mdx` (new HTTP route), `reference/cli/page.mdx` (`login`/`logout`/`ops cost-export`), `configuration/page.mdx`, `deployment/page.mdx` (new ops credential), and **`project-status/page.mdx`**, which CLAUDE.md requires to stay truthful.

---

## 3. Nits (non-blocking)

- **N1 — allowlist omits content-free fields worth having.** `tier` and `component_version` exist on `cost_ledger` and are content-free; the design drops them without saying why. Relatedly, `decisions.md:2309-2315` says every `cost_ledger` row names a `provider_call_id` — **no such column exists** in the shipped DDL (`p0_02_0002:139-160`). The design should note that divergence rather than inherit it silently, since a batched D58 call's pro-rata slices are exactly what an export consumer would want to dedupe.
- **N2 — `surface_cost_kind` as a PG enum** makes every future surface a migration plus five catalog edits. `resolve` is currently dead vocabulary (no embed). Consider `text` with a catalog-checked domain, or justify the enum explicitly.
- **N3 — `deployment_id` on every receipt** duplicates the page envelope; harmless, but say it's deliberate.
- **N4 — `remember ops cost-export --deployment <uuid>` should be optional.** The spine serves one deployment (D50); other `remember ops` subcommands take it, so match the existing grammar (`cli.py:120+`) but justify it.
- **N5 — no defined zero cursor.** "`next_cursor == cursor` when the page is empty" is undefined on the first call, where `cursor` was omitted.
- **N6 — `remember budget` now tells half the truth.** It is the existing "what am I spending" CLI (`cli.py:97-117`, reading `CostBudget` off `cost_ledger` only). The design amends neither the command nor its docs.
- **N7 — logout on an already-revoked token.** §6.1/§7 keep the file and exit non-zero on any revoke failure; a `401`/`404` (already revoked or expired) should be treated as success, otherwise a user can never complete `logout` and the stale file lives forever.
- **N8 — credential-mode refusal (§7)** needs a stated behavior on filesystems without POSIX modes.
- **N9 — state the `call_key` derivation rule, not just examples.** "`embed:search_claims:1`" is right; the binding rule should be *derived only from a fixed vocabulary of call sites, never from arguments*, so no future implementer interpolates a query fragment into a key that ships in the export.
- **N10 — §6.2's example credential file** hardcodes `https://remember.dev/app/api/dp/v1`; label it illustrative or use a placeholder origin, per Rule 3.
- **N11 —** the design should state affirmatively that **no `memory_v1` view is published over either ledger**. I checked: the sandbox query role only receives `SELECT` on `memory_v1` views (`p9_02_0023_query_space_roles.py:133-135`) while the blanket `GRANT SELECT ON ALL TABLES IN SCHEMA public` goes to the *view owner* (`:77-78`) — so the new table is **not** automatically exposed to open-query users. That's a point in the design's favor, and precisely why it should be written down: a later manifest addition would otherwise quietly put spend behind the customer perimeter that §5.2 works hard to avoid.

---

## 4. Missed alternatives & incorrect claims about current code

### Missed alternatives

- **M1 — the engine already has a published, versioned, content-free read surface.** Analysis §5's E1–E4 never considers exposing receipts as a `memory_v1` query-space relation (manifest-hashed, own role, saved-query governance, `spine/query_space` + `p9_02_0023`). It should be **named and rejected** — the rejection is easy (it sits behind the customer perimeter, which §5.2 correctly refuses) — but omitting the engine's own existing publication mechanism from a transport comparison is the gap a reviewer notices first.
- **M2 — extending the auth perimeter with a capability instead of minting a second secret.** `AuthPerimeterPort` returns an `AuthenticatedContext` with a `principal` (`ports/auth.py:10-16`); a scope/capability on that context would keep one credential model and one audit path, and would compose with the cloud's existing audited token mint (which §10 invokes to reject an engine-native login store — the same argument applies here). The blast-radius reasoning probably still wins, but the design asserts "This is **not** `AuthPerimeterPort`" without arguing against the strongest version of the alternative.
- **M3 — bind the export to a separate listener.** §5.2/§11 make the invariant depend on cloud proxy configuration ("a cloud proxy … must not forward `/ops/cost-export`"). A separate ops port or unix socket makes it physically unreachable from the customer path — an engine-enforced invariant instead of a documented obligation on someone else's config. That is materially more aligned with Rule 3.
- **M4 — one checked-in union VIEW as the read model.** §2 says "the export contract is the single read model," but §5.2 and §5.3 are two independent implementations of the same union SQL (HTTP producer and CLI producer). Two implementations of one contract drift. A `v_cost_receipts` view would give the catalog contract one named object, give E3 break-glass consumers the same allowlist, and let both producers be thin.

### Incorrect or unsupported claims about current code

| Claim | Status |
|---|---|
| analysis §1: `QueryEngine._embed` "returns `response.vectors[0]` only" | **Correct** (`query_engine.py:2666-2671`); and `EmbeddingResponse.usage` already carries the number (`model/model_provider.py:83-89`) — the analysis's "nothing new is required from the provider" holds. |
| analysis §3.4: operations share `QueryEngine` | **Correct** (`operation_executor.py:41,51,60,70,78`). |
| analysis §3.5: open query is a second embed path | **Correct** (`selfhost.py:247-269`, wired at `:454-458`). |
| design §11: head is `p9_10_0031` | **Correct.** |
| design §3: "`cost_usd` may be `NULL` … when the provider returned no usage object" | **Unreachable as specified.** `ProviderCallUsage` has no optional fields (`model/model_provider.py:29-38`) and §4.1's port takes a non-optional `usage`; the shipped adapter *raises* `ProviderAccountingError` rather than returning a usage-free response (`adapters/openrouter.py:676-687`). The nullable columns describe a state the port cannot produce. |
| design §11: "QueryEngine … constructor takes recorder + **deployment_id**" | **Conflicts with the existing surface.** `QueryEngine.__init__` has no `deployment_id` (`query_engine.py:181-214`); every public method takes it per call (`:216-223`, and each HTTP route passes it, `http_api.py:237-306`). Two sources of truth for a row's tenancy. Record from the method argument. |
| design §4.2: "Public `QueryEngine` method called in-process → same mapping as the HTTP verb that method serves" | **Has no answer for real methods.** `claims_about` (`:375`) and `claims_as_of` (`:437`) embed via `_rank_bounded_claims` (`:2261`) and serve **no** HTTP verb; `scan` (`:1959`) is the batch surface on a separate pool. The mapping rule silently falls through to §4.2's mint-on-missing. |
| design §11: "Catalog | `EXPECTED_TABLES` + enum list" | **Incomplete** — see B12. |
| design §1.3: no content "in application logs of those paths" | **False as shipped** — see B6. |
| design §4.4: `_LedgerCostMeter`/`record_call` are the only writers of `cost_ledger` | **Correct** (`_INSERT_COST` has exactly one call site, `work_ledger.py:807`). |

---

## 5. Implementation hazards left underspecified

- **H1 — contextvars and FastAPI's threadpool (the sharpest one).** Every route in `http_api.py` is a **sync** `def`, so FastAPI runs each in an anyio worker thread with a *copied* context. A request scope opened in a sync `Depends` is set inside that dependency's own threadpool context copy and is **not visible to the endpoint**, which runs as a separate `run_in_threadpool` call with its own copy. Only an async middleware (or async dependency) sets the value where the endpoint's copy can see it. §4.3 says "opens request scopes in `build_api` around every route that can embed" without naming the mechanism — and the obvious implementation is silently broken, with §4.2's mint-on-missing hiding the breakage: every embed gets its own `request_id`, per-request grouping quietly dies, and nothing errors. Require async middleware, a test that two embeds in one assured operation share a `request_id`, and a greppable log/counter whenever mint-on-missing fires.
- **H2 — concurrency inside one request.** If any surface ever embeds concurrently (threads or a task group), both the contextvar copy semantics and the `call_key` counter need explicit atomicity — and under B5's unique constraint, a counter race silently deletes a receipt.
- **H3 — union pagination will seq-scan `cost_ledger`.** Its only index is `ix_cost_budget_window (deployment_id, stage, lane, occurred_at)` (`p0_02_0002:163`), which cannot serve `WHERE deployment_id = … ORDER BY occurred_at, cost_id` across stages. The export needs a new index on `cost_ledger` — again touching the table the `Amends:` header says is untouched (see B2).
- **H4 — the rate limiter has no home.** §5.2's "1 request/second/credential" has no existing mechanism in `http_api.py`, and under multi-worker uvicorn an in-process token bucket is per-worker. Say where it lives and what it means with N workers.
- **H5 — the recorder is a second DB round trip on the hot path of every semantic query.** Latency budget, connection-pool behavior under saturation, and whether it shares the interactive pool are unspecified — notable because `QueryEngine` deliberately isolates the batch pool for exactly this reason (`query_engine.py:195-200`). Any batching/deferral answer interacts directly with B11.
- **H6 — `remember ops cost-export` output contract.** "Prints one `v1` page on stdout" — say: JSON only on stdout (logs to stderr), exit codes, and whether it pages or emits one page per invocation.
- **H7 — §8's test list has no test for the failures that matter.** Missing: customer token rejected *and* export still served during D74 admission closure (B3); a row committed after its `occurred_at` still exported (B2); decimal precision on a realistic embed cost (B1); a billed-but-failed embed (B4); meter-loss observability (B11); token-host derivation (B9).

---

## 6. Answers to the specific stress-tests

| Question | Verdict |
|---|---|
| Split `surface_cost_ledger` vs `cost_ledger` — correct, or is there something simpler? | **Split is correct.** D67's attribution is copied under a row lock from a *running* `processing_state` row; there is no honest way to hold a request there. But "two tables, one contract" is only honest if the union is sound — today it isn't (B1, B2, B5) and it's implemented twice (M4). |
| Does export auth stay off the customer perimeter given app-level `Depends`? | **Not as §11 states.** The "preferred" composition is unimplementable in FastAPI; only the mounted sub-app works, and even that leaves the invariant depending on cloud proxy config (B3, M3). |
| Request scope / contextvars — race or leak across concurrent requests? | **No cross-request leak** (anyio copies context per call), but a **silent loss** of the scope if it's opened in a sync dependency, hidden by mint-on-missing (H1). Concurrent embeds inside one request are the real race (H2, B5). |
| Meter persist failure vs user-visible query success — compatible with "honest ledger"? | **The choice is right; the contract is not.** Not failing the search is correct. But the loss is invisible to consumers, so §5.4's obligation can't be met (B11). |
| Login `token_host` derived from `api_url` — footgun? | **Yes**, and it also hardcodes a commercial control plane's URL layout into the library (B9). |
| `extra=forbid` vs contract versioning | **Insufficient.** It guards undeclared keys, not declared additions; and `v2` is named without any mechanism (B8). |
| Are `open_query` / `lookup` / `resolve` in scope, or smuggled cloud needs? | **In scope.** Self-host cannot answer "what did I spend" unless every engine embed is covered; `selfhost_embed_query` is a genuine second path. `resolve` is currently vocabulary for a call that doesn't exist — defensible, but say so. The actual smuggling risk is elsewhere: §6.3's `/dp/v1` derivation (B9) and analysis §8's supervisor-vocabulary mapping. |
| Catalog / schema / doc amendment completeness | **Materially incomplete** (B12). |
| Any content field that could leak into logs or rows? | **No content column in the design** — the row shape is clean. But §1.3's log claim is false as shipped (B6), and the `call_key` derivation rule needs to be stated, not just exemplified (N9). |
| Rule 2 phasing language | The prose is largely clean (no "v1/for now/MVP"), and "starting point to measure" is used correctly. The **substantive** Rule 2 violations are the deferred interactive ceiling and the missing retention/partitioning design (B7). |
| Is D91 needed, or amend D67? | **Both, and the design only plans one.** A new binding table, a published wire contract, and a new credential class are decision-grade — D91 is warranted, and `catalog_contract.DECISION_OBJECTS` needs the entry. But D67 must *also* be amended: as written it reads as the authority for provider spend, and after this change it governs worker spend only. A reader who finds D67 alone would conclude `cost_ledger` is the whole bill — which is the exact dishonesty this design exists to remove. |

**Recommended path to APPROVE:** fix B1–B6 and B9–B11 (mechanical, mostly one paragraph each), decide B7 explicitly (ceiling + retention, either designed or stated as a reasoned non-goal), design the version transition in B8, and expand the amendment list in B12. The architecture underneath does not need to change.
