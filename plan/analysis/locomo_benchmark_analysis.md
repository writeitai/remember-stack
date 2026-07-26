# WP-8.2 — LoCoMo full-system benchmark analysis

**Date:** 2026-07-23

**Status:** approved analysis; implementation prepared; no real benchmark run performed

## Recommendation

The primary RememberStack LoCoMo result must exercise the ordinary OSS memory system, not a
benchmark-specific claim-search shortcut. Use the named protocol **`RS-LoCoMo-Full-v3`**:

- exact pinned `locomo10.json` bytes from commit
  `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`;
- SHA-256 `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4`;
- categories 1–4 and the committed 8/200/1,540-question tiers;
- one isolated RememberStack deployment per conversation;
- one source document per conversation session;
- the complete implemented ten-route E/P1 lifecycle;
- fresh P2 graph and P3 corpus projections after ingestion;
- a bounded answer agent that sees only the question and the deployment's ordinary public
  recipe catalog;
- at most eight public tool calls and nine answer-agent model calls per question;
- frozen `openai/gpt-4o-mini` answer-agent and judge seats at temperature zero;
- judge accuracy as the primary metric and official deterministic LoCoMo F1 as secondary; and
- complete tool traces, response envelopes, model identities, component versions, costs, and
  failures in the run artifacts.

The former fixed `search_claims(k=30)` reader measured a useful claims-channel ablation, but it
did not measure the full retrieval logic. It bypassed entity resolution, current relation and
observation reads, graph traversal, hydration, recipes, typed negatives, and the grain/freshness
contract. It must not be published as the primary RememberStack result or retain the `J@30`
headline after the protocol changes.

## What “full system” means here

It means the complete normal ingestion and interactive retrieval path relevant to answering
LoCoMo:

```text
upload
  -> convert -> structure -> chunk -> embed_chunk -> extract_claims
  -> normalize_relations
       -> embed_claim
       -> adjudicate_supersession -> reconcile -> label_relation
  -> build P2 + P3
  -> public recipe tools
  -> bounded answer agent
```

It does not mean forcing unrelated operational scenarios into every QA item. Backfill, restore,
hard-forget, deletion, connector polling, and migration drills belong in WP-8.5's capability
suite.

Plane K is disclosed separately. The OSS K implementation requires routing rules, a knowledge
repository, and a reproducible planner/writer runtime. The stock Compose profile does not yet
provide those inputs. `pages_about` remains an honest public tool and returns a typed
`known_empty` when no K page exists, but `RS-LoCoMo-Full-v3` must not claim that K synthesis was
exercised. Adding K later creates a separately fingerprinted protocol.

P3 is likewise disclosed precisely: the stock deployment builds it and readiness proves the
build followed ingestion, but the remote recipe agent has no filesystem mount. The score does
not measure or claim P3 navigation. Adding a mount-enabled answer harness creates a separately
fingerprinted protocol rather than smuggling a benchmark-only file API into the OSS surface.

## Why the existing Compose profile was insufficient

The work ledger already implemented ten continuous document-version handlers, but the released
Compose profile ran only `convert` and `structure`. `chunk` was deliberately left pending.
Adding containers for every `PipelineStage` enum would also be wrong: several enum stages are
fused into implemented handlers, while others have no runtime handler.

The actual continuous routes are:

| Route | Work performed |
|---|---|
| `convert` | immutable Markdown/block representation |
| `structure` | PageIndex-style tree and placement |
| `chunk` | deterministic packed chunks |
| `embed_chunk` | context prefixes and chunk vectors |
| `extract_claims` | claim extraction plus grounding gates |
| `normalize_relations` | entity resolution, relations, observation adjudication |
| `adjudicate_supersession` | relation lifecycle decisions |
| `embed_claim` | P1 claims channel |
| `reconcile` | testimony currency, support recount, lifecycle events |
| `label_relation` | post-lifecycle relation/observation labels and P1 facts channel |

P2 and P3 are aggregate rebuilds, not per-document queue handlers. They run once after the
selected ingestion set has settled.

## Correctness issues found during review

### Missing fan-out join and fact-label race

Normalization previously enqueued supersession, claim embedding, and fact labeling in parallel,
while reconciliation followed only supersession. A fact label could therefore be indexed before
supersession changed its status, and “reconcile succeeded” did not mean the other terminal
branches had completed.

The smallest correct ordering is:

```text
normalize
  ├─ embed_claim
  └─ adjudicate_supersession -> reconcile -> label_relation
```

Readiness joins both terminal branches by checking all ten exact component generations. This
keeps claim embedding parallel but prevents fact labels from racing lifecycle state.

### Writer/query model drift

The API hard-coded the Qwen embedding model while P1 writers used `P1Settings`. A deployment
override could write vectors with one model and query them with another. The self-host API and
writers now load the same `REMEMBERSTACK_P1_EMBEDDING_MODEL` setting, and readiness reports all
current non-secret serving-process model bindings. This is configuration evidence, not
processing-time provenance; a benchmark deployment must keep one frozen Compose environment.

### Manual readiness was not evidence

The old harness accepted `--confirm-index-ready <sample>`. That proved only that an operator
typed a string. The normal API now exposes a read-only readiness report for bounded version IDs:
every expected stage/version must be terminal and P2/P3 builds must have begun after the latest
terminal stage. The harness checkpoints the report before any question is answered.

## Public retrieval protocol

The answer agent receives the current registry-rendered recipe descriptors, not internal Python
objects or database access. The stock self-host inventory includes:

- `resolve_entity`;
- current relations and observations;
- entity timeline;
- verbatim and hybrid claims;
- relation explanation/hydration;
- identity transcript and change feed;
- K page discovery;
- P2 neighborhood and shortest-path tools.

Every call is executed through `MemoryClient.run_recipe()`. The trace stores the tool name,
arguments, latency, and complete typed envelope. The agent must make at least one tool call,
cannot call an unlisted tool, and must finish in at most six words. Gold answers and gold
evidence never enter retrieval or answer-agent prompts.

## Dataset and comparability

The selected LoCoMo release contains ten conversations, 272 sessions, 5,882 turns, and 1,986
questions. Categories 1–4 contribute 1,540 scored questions; category 5 is excluded to match the
common conversational-memory QA setup. The dataset is CC BY-NC 4.0 and is not vendored.

Published “LoCoMo scores” are not one protocol: dataset revisions, ingestion units, top-k,
answer models, judge prompts, judge repetitions, and failure denominators differ. Therefore a
RememberStack result is comparable only with its full fingerprint. Vendor numbers remain
contextual until WP-8.3 reruns matched baselines.

## Cost and reproducibility

The maximum answer-side call count for `N` questions is `9N` answer-agent calls plus `N` judge
calls; actual tool and model counts are recorded. Ingestion calls remain governed by the normal
deployment ledger. A reproducible run pins explicit model IDs; rotating routers such as
`openrouter/free` are forbidden. A named free model that supports the required structured output
may be used for ingestion, but its exact ID belongs in the readiness artifact and produces a
distinct result configuration.

## Scope guardrails

- No benchmark-only SQL or search endpoint.
- No gold evidence in retrieval or answer context.
- No automatic dataset download or vendoring.
- No deployment creation, reset, or deletion in the harness.
- No real LoCoMo, API, OpenRouter, answer-agent, or judge call during implementation tests.
- No K claim unless a reproducible K runtime and artifacts are actually present.
- Claims-only retrieval may return later as a clearly labelled diagnostic, never the headline.
