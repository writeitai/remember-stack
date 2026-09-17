# OpenRouter provider rotation + 429 policy (chat path)

Binding design for D125. Analysis:
`plan/analysis/openrouter_inference_capacity_20260916.md`.
Status: accepted pending review; implementation follows in a stacked PR.

## Problem

The chat seat pins providers with `allow_fallbacks: false` and never rotates: when the
routed provider overloads, the adapter retry and the work-ledger retries all hit the same
engine, converting transient 429s into dead letters (R14: 45/997 from DeepInfra 429s).
Embeddings already rotate (ordered shortlist with fallbacks); chat does not.

## Decision

Bring the chat path to the embedding pattern, with three additions: overload-triggered
rotation, a split throttle budget, and per-call host recording.

## Contracts

### 1. Ordered chat shortlist with fallbacks

- New setting `chat_provider_order: list[str] | None` (same parsing as
  `embedding_provider_order`): ordered OpenRouter slugs, sent as
  `{"order": [...], "allow_fallbacks": true}`.
- Existing `chat_provider_only` keeps its exact current meaning (hard allowlist, no
  marketplace escape). Setting both is an error; setting neither keeps automatic routing.
- Default order (operator-overridable, mirrors the surveyed cheap/no-retain set):
  DeepInfra → Relace → Wafer. InferenceNet stays out of the default until its uptime
  stabilizes; GMICloud/StreamLake stay out while they retain prompts.

### 2. Rotate on overload, not just wait

- On 429 with an upstream-overload signal (`provider_error_code: engine_overloaded` or
  equivalent), the adapter advances to the next pinned slug instead of only sleeping and
  re-posting to the same routing. Retry-After is still honored as a floor.
- Rotation is bounded (at most one attempt per slug per logical call) so the existing
  "one provider call per logical call" accounting invariant bends in a recorded way, never
  silently: every attempt is recorded with its serving host (see 4).

### 3. Throttle budget separate from work attempts

- 429s draw from a dedicated throttle budget (backoff with jitter, Retry-After honor) and
  do **not** consume the work ledger's attempt budget. A hot pool may delay work; it may
  not kill it. Non-429 errors keep current behavior exactly.
- Bounds are settings with conservative defaults, documented alongside the existing
  `_IN_FLIGHT_BUDGET_RETRIES` so operators can reason about worst-case latency (R14 showed
  ~13 s per failed call; the design must keep worst-case bounded and visible).

### 4. Record the serving host per call

- `GenerationRecord` gains the serving provider/host name; the recorder emits it as a
  `locomo.provider_host`-style attribute next to `locomo.run_tag`. Today's autopsy could
  name DeepInfra only because the error body said so; successes must record it too.
- Benchmark comparability rule (binding): rotating hosts serving the **same pinned model +
  params** does not change `EXPECTED_INGEST_MODEL_BINDINGS` or the surface fingerprint,
  which pin models, not hosts. The implementing PR must assert fingerprint stability in
  tests; if a fingerprint moves, the design is wrong, not the test.

### 5. Zero-retention enforcement

- Chat payloads send `data_collection: deny`, and ZDR-only routing (`zdr: true`) is a
  supported operator flag. Provider picks already exclude retaining hosts by default (see
  order above); the flag is the enforcement, not the pick.

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
