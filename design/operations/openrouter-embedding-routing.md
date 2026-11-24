# OpenRouter embedding provider routing (deployment policy)

**Status:** Operational policy for self-host / compose / benchmark stacks.  
**Model:** `qwen/qwen3-embedding-8b` (same id across hosts — one embedding space).  
**Code:** `REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ONLY`,
`REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER`,
`REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER` and
`REMEMBERSTACK_OPENROUTER_ZDR` in `src/rememberstack/adapters/openrouter.py`.  
**Binding contract:** [provider rotation design §5](../../plan/designs/openrouter_provider_rotation_design.md#5-zero-retention-enforcement).  
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

## Policy (zero-data-retention deployments, e.g. remember.dev)

Customer content may reach only zero-data-retention (ZDR) endpoints. Use the
hard allowlist, not the order, and turn on ZDR:

```bash
REMEMBERSTACK_OPENROUTER_ZDR=true
REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ONLY=nebius,deepinfra
REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER=
REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER=
```

Each embedding request then sends
`{"only": ["nebius", "deepinfra"], "allow_fallbacks": true, "data_collection": "deny", "zdr": true}`:
OpenRouter picks between the two hosts and fails over only between them, and
`zdr`/`data_collection` filter every candidate, fallbacks included. SiliconFlow
is excluded: an order-only shortlist let production traffic fail over to it.
Confirm that each listed host is ZDR for this model on OpenRouter before
changing the list. Also set the OpenRouter account (or the guardrail on every
API key the deployment uses) to enforce ZDR, with Safety Retention excluded,
for **All other models**, the group `qwen/qwen3-embedding-8b` belongs to.
OpenRouter's ZDR settings are per model group (OpenAI, Anthropic, Google,
SpaceXSI, All other models), so enforce ZDR for the groups of the other roles
too (OpenAI for the default chat and image-description models). The request
flag follows those settings: a Safety Retention endpoint may keep any request,
flagged or not, for abuse prevention, self-serve accounts allow it by
default, and no request field can turn it off. Verify the settings for each
group and key before sending customer content.

With Docker Compose, set the allowlist, `REMEMBERSTACK_OPENROUTER_ZDR` and
`REMEMBERSTACK_IMAGE_DESCRIPTION_ZDR` in the project `.env`. `compose.yaml`
deliberately does not list them under `environment:`, so the services read
them only from `.env` through `env_file`; a shell export or an alternate
`--env-file` cannot override or clear them. (The embedding order and pin are
still interpolated, so keep them empty in every env file you use.) Image
descriptions need `REMEMBERSTACK_IMAGE_DESCRIPTION_ZDR=true` separately; it is
part of the image route's version, so turning it on refreshes the vision lane
once.

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
| `EMBEDDING_PROVIDER_ONLY` with 2+ hosts | `{ "only": [...], "allow_fallbacks": true }` (fallback stays inside `only`) |
| `EMBEDDING_PROVIDER_ONLY` with 1 host | `{ "only": [name], "allow_fallbacks": false }` |
| `EMBEDDING_PROVIDER_ONLY` + same-host `EMBEDDING_PROVIDER_ORDER`, 2+ hosts | `{ "order": [...], "only": [...], "allow_fallbacks": true }` |
| `EMBEDDING_PROVIDER_ONLY` + same-host `EMBEDDING_PROVIDER_ORDER`, 1 host | `{ "order": [name], "only": [name], "allow_fallbacks": false }` |
| `EMBEDDING_PROVIDER_ORDER` set (no allowlist) | `{ "order": [...], "allow_fallbacks": true }` (may fall back to any host) |
| only `EMBEDDING_PROVIDER` set | `{ "only": [name], "allow_fallbacks": false }` |
| none | omit `provider` (full OpenRouter auto routing — avoid for priced smokes) |

`REMEMBERSTACK_OPENROUTER_ZDR=true` adds `"data_collection": "deny"` and
`"zdr": true` to every row, and sends `{ "data_collection": "deny", "zdr": true }`
when nothing else is set.

When **both** order and hard pin are set, **order wins**. The allowlist cannot
be combined with the hard pin, and an order paired with the allowlist must name
exactly the same hosts once; the engine refuses to start otherwise.

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
