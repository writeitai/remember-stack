# OpenRouter embedding provider routing (deployment policy)

**Status:** Operational policy for self-host / compose / benchmark stacks.  
**Model:** `qwen/qwen3-embedding-8b` (same id across hosts — one embedding space).  
**Code:** `REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER` and
`REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER` in
`src/rememberstack/adapters/openrouter.py`.  
**Retrieved:** OpenRouter model endpoints API,
`GET /api/v1/models/qwen/qwen3-embedding-8b/endpoints`, 2026-08-06.

---

## Goal

Bound **price** by preferring the cheapest hosts of the **same** embedding
model, while allowing OpenRouter to **fail over** on 429/5xx so a single host
outage does not dead-letter `embed_chunk` / `embed_claim` / fact embedding.

Unrestricted “no `provider` object” routing is rejected for production smokes:
marketplace defaults can shift traffic toward higher-priced hosts without
notice.

---

## Endpoints observed (2026-08-06)

| Preference | Provider slug | List price (prompt) | Uptime (30m / 1d) | Notes |
| --- | --- | --- | --- | --- |
| 1 | `nebius` | **$0.01 / M tokens** | 100% / ~99.99% | Current historical default for this repo |
| 2 | `deepinfra` | **$0.01 / M tokens** | 100% / ~100% | Same price band; second host for failover |
| 3 | `siliconflow` | **$0.04 / M tokens** | ~99.99% / ~99.97% | 4× price; last shortlist hop before marketplace |

Latency/throughput percentiles were **null** on the endpoints payload at
retrieval time; order is therefore **price first**, then uptime, then
familiarity (Nebius before DeepInfra at equal price).

Endpoint **tags** such as `siliconflow/fp8` are quant/endpoint labels, not
routing slugs. Use base slugs in env (`siliconflow`, not `siliconflow/fp8`).

Only these three providers served this model id at retrieval. Re-check the
endpoints API when refreshing the shortlist.

---

## Policy (compose / smoke / local)

```bash
# Preferred: ordered shortlist + allow_fallbacks (implemented when ORDER is set)
REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER=nebius,deepinfra,siliconflow

# Leave hard pin empty when using ORDER (ORDER wins if both are set, but pin
# alone is allow_fallbacks=false and will not fail over).
REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER=
```

Semantics in the adapter:

| Env | OpenRouter `provider` payload |
| --- | --- |
| `EMBEDDING_PROVIDER_ORDER` set | `{ "order": [...], "allow_fallbacks": true }` |
| only `EMBEDDING_PROVIDER` set | `{ "only": [name], "allow_fallbacks": false }` |
| neither | omit `provider` (full OpenRouter auto routing — avoid for priced smokes) |

When **both** order and hard pin are set, **order wins**.

---

## When to hard-pin

Use `REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER=nebius` (only) only for:

- A/B experiments that must freeze one host, or  
- Incident isolation when a specific host must be excluded via account
  settings and a single remaining host is intentional.

Hard pin is **not** the default for multi-hour ingest drains.

---

## Refresh procedure

1. `GET https://openrouter.ai/api/v1/models/qwen/qwen3-embedding-8b/endpoints`
   with a valid API key.
2. Rank remaining **status=0** endpoints by prompt price ascending, then
   uptime, then observed latency if present.
3. Update this document’s table, the example env line, and any deploy
   manifests that copy the shortlist.
4. Record retrieval date in this file.

---

## Related

- Dynamic health-based reordering across workers (not implemented):  
  `design/proposals/provider-health-routing.md` (when present on the branch).
- Benchmark runbook env names: `design/benchmarks/runbook.md`.
