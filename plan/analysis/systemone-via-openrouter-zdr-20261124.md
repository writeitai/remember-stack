# System One (Jev) through OpenRouter under zero data retention

Non-binding analysis, 2026-11-24. Binding result:
[Jev design §2](../designs/jev_adjudication_design.md#system-one-through-openrouter)
and the D126 routing clarification in [decisions.md](../../decisions.md).

## Problem

The owner requires remember.dev to send customer content only to
zero-data-retention (ZDR) endpoints. A ZDR endpoint is one whose host does
not store prompts or outputs after answering. With
`REMEMBERSTACK_FACT_ADJUDICATION_ENGINE=jev`, fact adjudication sends entity
facts and new claims (customer content) to TypeSafe's System One model, Jev.
The engine called only TypeSafe's own API, with one process-wide key from
`REMEMBERSTACK_TYPESAFE_API_KEY`.

That leaves two gaps:

1. **Retention.** The engine had no way to ask for a ZDR endpoint for Jev.
2. **Per-deployment keys.** remember.dev runs many deployments (Projects) in
   one shared runtime. Each one has its own `EngineSettings` and its own
   OpenRouter child key (`model_provider=OpenRouterSettings(api_key=...,
   zdr=True)`). But the composition root built `TypeSafeSettings()` from the
   process environment, so every Project would share one TypeSafe key and one
   bill.

## What OpenRouter offers (verified 2026-11-24)

From <https://openrouter.ai/docs/guides/community/typesafe-sdk.md>, the
System One SDK README, and the Decisions API reference:

- `POST https://openrouter.ai/api/v1/systemone` takes TypeSafe's request
  shape (`model`, `state`, `questions`) and returns TypeSafe's response shape
  (`model`, `answers`, `usage`), plus `id` and `provider`. `usage` carries
  `input_tokens`, `output_tokens` and `cost` (a number, in credits; one credit
  is one US dollar).
- The bare id `jev-latest` routes to `~typesafe/jev-latest`. The response
  names the dated serving model, for example `typesafe/jev-1.13-20260917`.
- The request accepts the same `provider` preferences object as chat,
  including `zdr` and `data_collection`.
- OpenRouter lists two Jev endpoints, `typesafe` and `typesafe/zdr`. Only
  `typesafe/zdr` appears in `GET /api/v1/endpoints/zdr`, with
  `safety_retention: false`. Without a ZDR preference, routing may pick the
  endpoint that is not ZDR.
- The documented reference example costs `0.000019992` for 476 input tokens:
  exactly TypeSafe's list price of $0.042 per million input tokens, with
  output tokens free.

## Options considered

1. **Point `REMEMBERSTACK_TYPESAFE_BASE_URL` at OpenRouter.** This works
   without code changes, but sends no ZDR preference, still uses one
   process-wide key (an OpenRouter key placed in the TypeSafe variable), and
   prices calls locally. Rejected: it closes neither gap.
2. **Add a separate `TypeSafeSettings.zdr` flag.** This closes the retention
   gap only. Each deployment would still need its own key, and ZDR would then
   be configured in two places that can disagree. Rejected.
3. **An explicit route that reuses the deployment's OpenRouter settings
   (chosen).** `REMEMBERSTACK_TYPESAFE_ROUTE=openrouter` makes the
   composition root build the System One client from the deployment's
   `OpenRouterSettings`: its key, base URL and `zdr` flag. That is the same
   object that already governs chat and embeddings, so one ZDR switch covers
   every OpenRouter call, and each Project bills its own child key. The
   default route, `typesafe`, is byte-for-byte the previous behaviour.

## Choices inside the chosen option

- **Payload.** Under ZDR, every request adds
  `"provider": {"zdr": true, "data_collection": "deny"}`. These are the same
  two fields #630 added to embeddings. The TypeSafe route never sends
  `provider`, because TypeSafe's own API has no such field. A deployment with
  `zdr` on is therefore refused on that route (the profile checks it when it
  is built), instead of silently sending Jev content to TypeSafe's own API.
- **No `only` allowlist.** Jev is served only by TypeSafe on OpenRouter, so
  `only: ["typesafe"]` would restrict nothing that `zdr: true` does not
  already restrict, since `zdr` selects `typesafe/zdr` among TypeSafe's two
  endpoints. It would also make every call fail if OpenRouter renamed the
  provider slug. Add an allowlist only if another host starts serving Jev.
- **Cost.** On the OpenRouter route the billed `usage.cost` is required, the
  same contract as OpenRouter chat. A response without it raises
  `ProviderAccountingError`; the engine does not guess. Tokens are still read
  from `input_tokens` and `output_tokens`. The serving model is recorded as
  OpenRouter names it (`typesafe/...`), without a second `typesafe/` prefix.
- **Backup.** The optional OpenAI Decisions backup calls OpenAI's own API,
  outside OpenRouter's ZDR routing. With ZDR on, the profile refuses
  `REMEMBERSTACK_TYPESAFE_BACKUP=openai_decisions` when it is built.
- **Readiness bindings.** `systemone_route` is reported only when it is not
  the default, so direct-TypeSafe deployments and benchmark protocols keep
  byte-identical bindings. ZDR stays out of the bindings, as D127 decided for
  OpenRouter transport settings.
- **Reuse keys (D56).** Unchanged. Jev's generation identity names the model
  and policy, not the route or key, and no paid extraction or embedding key
  is touched.

## Unverified

The tests use mocked transports. Before production use, one live call should
confirm three things: the call was served by TypeSafe's ZDR endpoint,
`usage.cost` is present, and OpenRouter's 429 and 529 error bodies classify as
before (they carry no `answers` or `usage`). The response's `provider` field
names TypeSafe for both endpoints, so the endpoint check needs other evidence
that distinguishes `typesafe/zdr` from `typesafe`.

The request flag is also not sufficient on its own: OpenRouter combines
`zdr: true` with the account's or key guardrail's Safety Retention policy,
set separately for Google models and All other models (Jev is in the second
group), and self-serve accounts allow Safety Retention by default. Strict zero
retention needs that setting excluded and verified for every Project key.
OpenRouter's own CSAM screening may still retain flagged content where the law
requires, ZDR traffic included; that is a terms question for the owner, not a
setting.
