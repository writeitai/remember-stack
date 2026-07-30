# WP-8.2 — LoCoMo full-system benchmark analysis

**Date:** 2026-07-23

**Status:** approved analysis; implementation prepared; no real benchmark run performed

## Recommendation

The primary RememberStack LoCoMo result must exercise the ordinary OSS memory system, not a
benchmark-specific claim-search shortcut. Use the named protocol **`RS-LoCoMo-Full-v6`**:

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
`known_empty` when no K page exists, but `RS-LoCoMo-Full-v6` must not claim that K synthesis was
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

### The session time was rendered but not wired into ingestion

The pinned dataset's `session_N_date_time` values contain a calendar date and wall-clock time
such as `1:56 pm on 8 May, 2023`, but no UTC offset or timezone identifier. This is an omission
in the source data, not a missing RememberStack deployment setting. The dataset adapter retained
the literal value in `LoCoMoSession.timestamp` and the renderer wrote it into Markdown, while
the binding ingestion mapping deliberately omitted `source_modified_at` rather than inventing a
zone (`benchmarks/locomo/dataset.py`, `benchmarks/locomo/protocol.py`, and the former §3 of
`plan/designs/locomo_benchmark_design.md`).

That policy became an integration defect when E2's temporal grounding contract was tightened:
relative dates may resolve only from an absolute timestamp in the deterministic document header
(`plan/designs/e2_e3_claims_relations_design.md` §3; `src/rememberstack/workers/e2.py`
`_header_text`). The header is sourced from structured `source_modified_at` or `published_at`,
not by reparsing arbitrary body text. Consequently, LoCoMo sessions were extracted with `date
unknown` even though the adapter held a useful session wall time.

The selected repair is adapter-scoped assumed UTC:

- parse every actual session timestamp during local dataset validation using an explicit
  locale-independent grammar;
- assign UTC because the source supplies no offset;
- retain the raw source string;
- persist `source_modified_at` plus `source_timezone_basis=assumed_utc` in `documents.json`;
- disclose the assumption in rendered Markdown; and
- pass the aware UTC value through the ordinary `MemoryClient.ingest()` argument.

This is a convention, not a claim about the participants' civil timezone. It preserves the
dataset's date and clock ordering deterministically and makes the uncertainty auditable. A
malformed timestamp fails local preparation before any API or model call.

Implementation validation on 2026-07-30 loaded bytes matching the pinned SHA-256 and parsed all
10 samples and 272 actual sessions; every timestamp matched the grammar and produced an aware
UTC value with the `assumed_utc` basis. The dataset remains unvendored, so CI covers the grammar
with synthetic boundaries while operator preparation revalidates every pinned timestamp.

UGM itself remains fail-closed. The SDK and HTTP API reject naive or non-UTC source timestamps,
watched-source items and durable upload records use the shared `UTCDateTime` validator, and E0
validates an observed upload before persisting its raw bytes. A wholly absent source timestamp
remains unknown; it is never replaced by ingestion time.

Review also exposed a pre-existing metadata-only no-op hazard. When identical bytes arrived
under a new connector revision, the catalog advanced both `source_version_ref` and
`source_modified_at` on the already-processed version. That could make the version row disagree
with E2's stored deterministic header and derived claim times, or erase a known time with
`NULL`. The core repair keeps the timestamp immutable on content-hash no-ops and advances only
the connector cursor (`src/rememberstack/spine/document_catalog.py`; D55 clarification).

Rejected alternatives were using ingestion time (wrong event time), teaching E2 to parse
benchmark prose (benchmark leakage into the engine), leaving the header unknown (continued
relative-time failures), and globally coercing every naive SDK datetime to UTC (silently wrong
for ordinary sources whose wall time belongs to a known local zone). Only this source adapter
owns the fallback.

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
