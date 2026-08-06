# Proposal: Shared provider health table for dynamic OpenRouter routing

**Status:** Open proposal — **not binding**, not implemented.  
**Date:** 2026-08-06  
**Related shipped work:** ordered embedding shortlist with per-request
fallbacks (`REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER`; policy in
`design/operations/openrouter-embedding-routing.md`).  
**Problem origin:** Parallel smokes / multi-worker drains all hard-pinned to
one host (e.g. Nebius embeddings, BaseTen chat) correlated 429/5xx into stage
dead-letters; operators want **dynamic switch** on error and slowness without
unbounded marketplace pricing.

---

## 1. Goal

When OpenRouter inference hosts misbehave (errors, rate limits, elevated
latency), **all workers for a deployment** should:

1. Prefer healthier / faster hosts from an **allowed shortlist** (price-bounded).
2. Demote bad hosts for a cooldown window.
3. Recover automatically when the host looks healthy again.
4. Never silently switch to a **different embedding model** (vector space).

This is **adaptive preference among allowed providers**, not “no provider
object / full auto.”

---

## 2. What already exists (baseline)

| Capability | Today |
| --- | --- |
| Embedding hard pin | `EMBEDDING_PROVIDER` → `only` + `allow_fallbacks: false` |
| Embedding ordered shortlist | `EMBEDDING_PROVIDER_ORDER` → `order` + `allow_fallbacks: true` (failover **within one HTTP request** via OpenRouter) |
| Chat ordered shortlist | Same pattern if/when enabled for chat |
| Cross-call learning | **None** — every call rebuilds the same static order |
| Shared state across workers | **None** — each process is independent |
| Per-deployment diversity | **None** — all deploys with the same env stampede the same first hop |

OpenRouter may retry other hosts **inside** a single request when
`allow_fallbacks` is true. That does **not**:

- remember that Nebius was slow for the next 500 calls,
- coordinate four `worker-embed-claim` replicas,
- or let ops “disable DeepInfra for an hour” without redeploying env.

---

## 3. Proposed design

### 3.1 Concepts

**Allowed set + default order (policy)**  
Config (env today; later DB): e.g.  
`nebius,deepinfra,siliconflow` for `qwen/qwen3-embedding-8b`.  
Same model id only for embeddings.

**Health score (runtime)**  
Per key approximately:

```text
(deployment_id, modality, model_id, provider_slug)
```

Fields (illustrative):

| Field | Meaning |
| --- | --- |
| `success_count` / `error_count` | Rolling or EWMA |
| `latency_ewma_ms` | Recent latency |
| `last_error_at` / `last_success_at` | Recovery clocks |
| `cooldown_until` | Do not prefer until this time |
| `circuit_state` | closed / open / half-open (probe) |

**Request-time order**  
Sort allowed providers by score (errors and latency), stable-shuffle with
optional `deployment_id` salt when scores tie, then send:

```json
{ "order": ["deepinfra", "nebius", "siliconflow"], "allow_fallbacks": true }
```

Still **never** an empty provider constraint that allows arbitrary expensive
hosts outside the allowed set — use `order` with `allow_fallbacks: true`
**or** `order` + `allow_fallbacks: false` limited to the shortlist only
(product choice: prefer true so OR can escape to other hosts of the **same
model** if all shortlisted fail; document cost risk).

**Recommendation for v1:** `order` = scored shortlist, `allow_fallbacks: true`
so total outage of the three still can complete; accept possible price
escape only when the shortlist is fully unhealthy. Alternative v1b: false
and fail the call for ledger retry.

### 3.2 Shared store

| Option | Pros | Cons |
| --- | --- | --- |
| **Postgres table** (same spine DB) | Already deployed; transactional; multi-worker | Hot path writes; need TTL/cleanup |
| Redis | Natural TTL, fast | New dependency for self-host |
| In-process only | Zero infra | No coordination |

**Proposal default:** Postgres table in the deployment’s database, updated
asynchronously or with short debounced upserts so embed hot path does not
await a heavy write on every call.

Sketch:

```sql
CREATE TABLE provider_route_health (
  deployment_id   uuid NOT NULL,
  modality        text NOT NULL,  -- 'chat' | 'embedding'
  model_id        text NOT NULL,
  provider_slug   text NOT NULL,
  success_ewma    double precision NOT NULL DEFAULT 1.0,
  error_ewma      double precision NOT NULL DEFAULT 0.0,
  latency_ewma_ms double precision,
  cooldown_until  timestamptz,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, modality, model_id, provider_slug)
);
```

Optional second table for **static policy** (allowed order, human overrides)
when env is no longer enough:

```sql
CREATE TABLE provider_route_policy (
  deployment_id   uuid PRIMARY KEY,
  embedding_order text[],  -- preferred shortlist
  chat_order      text[],
  allow_fallbacks boolean NOT NULL DEFAULT true,
  updated_at      timestamptz NOT NULL DEFAULT now()
);
```

### 3.3 Update rules (normative intent)

On each completed provider call (when OpenRouter returns which host served,
or when we infer from error metadata):

| Outcome | Health update |
| --- | --- |
| 2xx, latency ≤ target | success↑, latency EWMA↓, clear cooldown if half-open |
| 429 / 5xx / timeout | error↑, set `cooldown_until = now() + backoff` |
| Latency ≫ p95 target | latency EWMA↑; demote in sort without full circuit open |

**Backoff:** exponential with jitter, capped (e.g. 30s → 15m).  
**Half-open:** after cooldown, allow a probe fraction (or one worker) to try
the demoted host.

### 3.4 Read path

1. Load policy (env or `provider_route_policy`), cache in process 30–60s.  
2. Load health rows for `(deployment, modality, model)`, cache 5–15s.  
3. Build `order`.  
4. Call OpenRouter.  
5. Record outcome (queue / async upsert).

Workers must tolerate missing health rows (cold start = policy default order).

### 3.5 Multi-deployment / parallel smokes

- Health is **per `deployment_id`** so customer A’s storm does not poison B.  
- Optional **order salt** = hash(deployment_id) to diversify default first hop
  when all hosts are healthy (reduces thundering herd on Nebius).  
- Benchmark shards: separate deployment ids or explicit different policy rows.

### 3.6 Embeddings vs chat

| | Embeddings | Chat |
| --- | --- | --- |
| Fail over hosts of **same model** | Yes | Yes |
| Fail over to **different model** | **No** (index corruption) | Policy-only, rare |
| Price sensitivity | High (bulk tokens) | Medium |

---

## 4. Non-goals

- Replacing OpenRouter with direct multi-vendor SDKs in v1.  
- Storing API keys in the health/policy tables.  
- Global single-row “current_provider” flip (correlated and brittle).  
- Using ANN/Lance for routing decisions.

---

## 5. Alternatives considered

| Alternative | Why not enough alone |
| --- | --- |
| Static `EMBEDDING_PROVIDER_ORDER` only | No cross-call learning; still prefers first slug forever when it is “slow but succeeding” |
| Unset provider (full auto) | Price unbounded relative to shortlist |
| In-process demotion only | Four embed workers re-learn independently; thundering herd remains |
| Control-plane UI without health | Manual only; misses auto-recovery |

---

## 6. Adoption triggers

Implement when:

- Multi-worker or multi-shard drains regularly hit host-specific 429/5xx
  after static order is already in place, or  
- Latency cliffs on one host burn wall-clock while others are fine, or  
- Multi-tenant cloud needs per-deployment routing without redeploy.

Defer while:

- Single-worker dogfood + static order + OR intra-request fallbacks suffice.

---

## 7. Suggested sequencing

| Step | Work |
| --- | --- |
| 0 | **Done / parallel:** static embedding order + fallbacks + written policy |
| 1 | Parse OpenRouter response `provider` field into cost_ledger or metrics |
| 2 | Postgres health table + process cache + sort `order` |
| 3 | Cooldown + half-open probes |
| 4 | Optional `provider_route_policy` + admin/ops CLI |
| 5 | Chat modality parity |

---

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| Health write amplification | Debounce / sample; EWMA in memory flush every N seconds |
| Wrong provider label from OR | Treat missing provider as “unknown”; do not demote randomly |
| Over-demotion flaps | Hysteresis; minimum sample count before open circuit |
| Price escape with allow_fallbacks | Document; optional strict shortlist-only mode |
| Stale cache after deploy policy change | Short TTL; version policy row |

---

## 9. Success metrics

- Fewer `embed_*` / chat stage dead-letters attributable to single-host 5xx.  
- p95 embed latency under multi-hour drain does not stick to a degraded host.  
- $ / M embedding tokens stays within a band of the shortlist’s cheapest
  two hosts under healthy conditions (measure via cost_ledger + provider
  label when available).

---

## 10. References

- Adapter: `src/rememberstack/adapters/openrouter.py`  
- Embedding policy: `design/operations/openrouter-embedding-routing.md`  
- OpenRouter provider routing docs:
  https://openrouter.ai/docs/guides/routing/provider-selection  
- BEAM 100K smoke: Nebius-only embed DLQ (500/503); BaseTen chat 429s under
  hard pin
