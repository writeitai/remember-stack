# Inference capacity: R14 evidence, provider survey, alternatives

Non-binding analysis for D125. Evidence date: 2026-09-16. External facts retrieved
2026-09-17 (prices and policies change; re-check before spending).

## The question

LoCoMo R14 (conv-42, GLM on OpenRouter) finished with 45 dead-lettered work items out of
997. Is this a model/prompt problem or a capacity problem, and what fixes it as we scale?

## Evidence

Full autopsy: `/root/r14/r14-autopsy.md` (box; log + Langfuse method inside).

- 952 succeeded, 45 dead-lettered (all at attempt 3/3), 148 retries. 23 items left staging.
- Langfuse (`run_tag = r14-glm-c42`, 1,225 generation traces, full payloads): **all 193
  failed calls are the same error** — HTTP 429, `provider_name: DeepInfra`,
  `provider_error_code: engine_overloaded`, `is_byok: false` (shared OpenRouter pool).
- 148 retries + 45 dead = 193 failed calls, matched exactly per stage. Zero validation
  errors, zero refusals, zero malformed JSON. Nothing is wrong with prompts or schemas.
- Throttling ran all day with a burst at 17:xx UTC (115/193). Each 429 still cost a median
  13.3 s, a large share of the 8.7 h runtime.
- Spend for the whole run: ~$0.32. Capacity, not money, is the constraint.

## The mechanism (code)

`src/rememberstack/adapters/openrouter.py`:

- Chat pins providers with `{"only": [...5 slugs...], "allow_fallbacks": false}`. OpenRouter's
  router kept picking DeepInfra; when it overloaded, nothing moved (all 193 failures name it).
- `_post` retries 429s with a bounded wait, then the work ledger retries the item twice more —
  but every retry sends the same payload into the same routing, so all three attempts hit the
  same overloaded engine. Retries without rotation manufactured the dead letters.
- Embeddings already have the better shape: ordered shortlist **with** fallbacks, so a 429
  moves to the next host (`_embedding_provider_payload`).

## Provider survey for `z-ai/glm-5.3-flash` (OpenRouter, 29 endpoints)

Price = $/M input tokens (output ~3.3x). Policy = OpenRouter's provider index
(`training` / `retainsPrompts`, self-reported). BYOK = key accepted. Fetched 2026-09-17.

| $/M in | provider | training | retains | BYOK | note |
|---|---|---|---|---|---|
| 0.075 | DeepInfra | no | no | yes | cheapest; the one that throttled us on shared pool |
| 0.090 | InferenceNet | no | no | yes | 5-min uptime 94.8% at fetch — watch |
| 0.090 | Relace | no | no | yes | 5-min uptime 99.99% |
| 0.100 | Wafer | no | no | yes | 5-min uptime 99.95% |
| 0.105 | GMICloud | no | **yes** | yes | fails zero-retention |
| 0.132 | Novita | no | no | yes | |
| 0.141 | StreamLake | no | **yes** | yes | fails zero-retention |

BYOK economics (OpenRouter BYOK docs, retrieved 2026-09-17): provider bills you directly;
OpenRouter takes 5% of list price from credits, inside a $25k/mo list-price allowance on
pay-as-you-go. At $0.32/run the fee is ~$0.016/run and the allowance is never touched.
What BYOK buys is **your own rate limits**, plus per-key fallback to shared capacity.

Zero-retention enforcement is a request flag (`zdr: true`, `data_collection: deny`), not just
a provider pick — the design wires it in.

## Alternatives considered

1. **BYOK keys only (DeepInfra + Relace + Wafer), no code change.** Kills the shared-pool
   throttle; each key is an independent pool with automatic shared fallback. Cheap,
   immediate. Loses if one key's provider overloads its own engine — no rotation still.
2. **Adapter rotation on overload (chosen).** Ordered shortlist + fallbacks for chat,
   mirroring embeddings; rotate on 429/`engine_overloaded`; record serving host per call.
   Same model + same params ⇒ benchmark-comparable. Fixes the manufactured-dead-letter
   mechanism for every caller (product, benchmarks, experiments).
3. **429 policy split.** Throttles get their own attempt budget, Retry-After honor, backoff
   with jitter — instead of burning the 3 work attempts on a hot pool. Complement to (2).
4. **First-party APIs (Z.AI direct for GLM; Vertex already covers Gemma).** First-party
   quota, no middleman margin. Real scaling answer long-term; requires new adapter +
   credential plumbing. Deferred until volume justifies, not a substitute for (2).
5. **More parallelism (rejected).** Pushing harder into a throttled pool makes more 429s;
   the 17:xx burst looks like exactly this.
6. **Switch models to dodge throttles (rejected).** Silently breaks benchmark comparability.
7. **Locomo-only routing fix (rejected).** Forks benchmark behavior from product behavior;
   the benchmark must measure the product. The flaw bites all engine traffic.

## Recommendation

Do (1) immediately in ops, implement (2)+(3) in the core engine adapter, keep (4) as the
tracked follow-up. Detail: `plan/designs/openrouter_provider_rotation_design.md`.
Decision: D125 in `decisions.md`.
