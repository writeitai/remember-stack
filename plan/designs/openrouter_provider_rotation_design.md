# OpenRouter provider rotation + 429 policy (chat path)

Binding design for D127. Analysis:
`plan/analysis/openrouter_inference_capacity_20260916.md`.
Status: accepted pending review; implementation follows in a stacked PR.

## Problem

The chat seat pins providers with `allow_fallbacks: false` and never rotates: when the
routed provider overloads, the adapter retry and the work-ledger retries all hit the same
engine, converting transient 429s into dead letters (R14: 45/997 from DeepInfra 429s).
Embeddings declare an ordered shortlist with OpenRouter-side fallbacks, but neither path rotates client-side on overload today.

## Decision

Bring the chat path to the embedding pattern, with three additions: overload-triggered
rotation, a split throttle budget, and per-call host recording.

## Contracts

### 1. Ordered chat shortlist with fallbacks

- New setting `chat_provider_order: list[str] | None = None` (same comma-separated-slug
  parsing as `embedding_provider_order`, new validator entry reusing
  `_parse_provider_name_list`). Env:
  `REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ORDER`. Sent as
  `{"order": [...], "allow_fallbacks": true}`.
- Default is `None`: a hardcoded provider list would break every other model on the
  shared adapter. The surveyed order DeepInfra → Relace → Wafer is the recommended
  **deployment/env-level** configuration for GLM (documented in the ops runbook, not in
  code). InferenceNet stays out of the recommendation until its uptime stabilizes;
  GMICloud/StreamLake stay out while they retain prompts.
- Existing `chat_provider_only` keeps its exact current meaning (hard allowlist, no
  marketplace escape). Setting both is an error; setting neither keeps automatic routing.
- Rotation operates on the effective slug list from **either** setting: on overload the
  adapter advances past the failed slug. A single-slug `only` has nowhere to advance, so
  it keeps today's behavior (throttle budget, then the typed error).

### 2. Rotate on overload, not just wait

- On 429 with an upstream-overload signal (`provider_error_code: engine_overloaded` or
  equivalent), the adapter advances to the next pinned slug instead of only sleeping and
  re-posting to the same routing. Retry-After is still honored as a floor.
- Rotation is bounded (at most one attempt per slug per logical call) so the existing
  "one provider call per logical call" accounting invariant bends in a recorded way, never
  silently: **every attempt emits its own `GenerationRecord` span** — intermediate 429s
  with `outcome = transport_error` (exactly how R14's 193 failures are visible today),
  the terminal attempt with its outcome plus serving host (see 4).

### 3. Throttle budget separate from work attempts

- 429s draw from a dedicated throttle budget (exponential backoff with jitter,
  Retry-After honor) and do **not** consume the work ledger's attempt budget. A hot pool
  may delay work; it may not kill it. Non-429 errors keep current behavior exactly.
- Exact settings on `OpenRouterSettings`, alongside the existing
  `_IN_FLIGHT_BUDGET_RETRIES` so operators can reason about worst-case latency (R14 showed
  ~13 s per failed call; the design must keep worst-case bounded and visible):
  - `chat_throttle_retries: int = 3` (env `REMEMBERSTACK_OPENROUTER_CHAT_THROTTLE_RETRIES`,
    dedicated 429 attempts per logical call, shared across slugs in the rotation).
  - `chat_upstream_overload_max_retry_after_s: float = 30.0` (env
    `REMEMBERSTACK_OPENROUTER_CHAT_OVERLOAD_MAX_WAIT_S`, per-wait cap; an explicit
    Retry-After below the cap wins, anything above is clamped to the cap).

### 4. Record the serving host per call

- `GenerationRecord` gains the serving provider/host name; the recorder emits it as a
  `locomo.provider_host`-style attribute next to `locomo.run_tag`. Today's autopsy could
  name DeepInfra only because the error body said so; successes must record it too.
- Discovery mechanism, in order: (a) the response-body `provider` field when present on
  the chat completion; (b) the generation lookup by response id through the existing
  `fetch_generation` plumbing (returns `provider_name`); (c) fallback to the rotation
  slug the attempt targeted. The implementing PR verifies (a)/(b) against the live API
  and tests the fallback; if neither field exists, the fallback is the mechanism.
- Benchmark comparability rule (binding): rotating hosts serving the **same pinned model +
  params** does not change `readiness.model_bindings` (`EXPECTED_INGEST_MODEL_BINDINGS`),
  `surface_manifest_hash`, or `protocol_fingerprint`, which pin models, not hosts.
  `chat_provider_order`, the throttle settings, and the ZDR flag are **transport settings
  and are excluded from `_model_bindings()` in `profiles/selfhost.py`** — unlike
  `embedding_provider_order`, which is in bindings today, so an implementer copying that
  precedent without this exclusion would break the benchmark equality gate. The
  implementing PR asserts all three fingerprints stable in tests; if one moves, the design
  is wrong, not the test.

### 5. Zero-retention enforcement

- Chat payloads always send `data_collection: deny` in the provider dict.
- `zdr: bool = False` (env `REMEMBERSTACK_OPENROUTER_ZDR`): when true, sends
  `zdr: true` to restrict routing to zero-data-retention endpoints. Provider picks
  already exclude retaining hosts by default (see order above); the flag is the
  enforcement, not the pick.

## Failure / recovery

- All pinned slugs overloaded: bounded waits, then the existing typed provider error
  surfaces and the work ledger retries later as today — no new unbounded loops.
- Rotation state is per logical call, never shared across workers: no cross-request
  coordination, no sticky state to go stale.
- Cost impact: BYOK 5% fee applies per OpenRouter's terms; usage accounting stays
  one-record-per-attempt so spend remains reconcilable (R14's 193 unaccounted failed calls
  stay visible as attempts, not gaps).

## Security / operational consequences

- Provider keys stay in GCP Secret Manager / GitHub Actions secrets and the OpenRouter
  dashboard (BYOK); secret **names** in `infra/`-style inventory only. No keys in repo,
  logs, or traces (the recorder never emits credentials; review must confirm the new host
  attribute carries no secret material).
- Operators get a 429-rate signal per host (the recorder attribute makes it queryable);
  alert on throttle rate, not only on dead letters.
- UMC picks this up via its pinned engine dependency bump; per-tenant key management is
  explicitly out of scope here (UMC productization follows).

## Alternatives (why not)

- Locomo-only routing: rejected — forks benchmark from product behavior.
- Model switching on throttle: rejected — breaks comparability silently.
- Pure BYOK with no rotation: accepted as the ops complement, insufficient alone (one
  key's engine can still overload).
- First-party Z.AI adapter: deferred follow-up, not a substitute.

## Implementation-facing notes

- Settings, parsing, and payload builders live in `adapters/openrouter.py` next to the
  embedding precedent; unit tests cover order/fallback payload shape, rotation bound,
  throttle-vs-work budget split, host recording, and fingerprint stability.
- Review bar: cursor + antigravity on the implementing PR, same as the design PR.
