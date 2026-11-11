# Answer-model comparisons on an unchanged processed store

Non-binding analysis, 2026-11-11. The owner requested a complete conv-42
comparison with `openai/gpt-6-luna-pro` at high reasoning after a fresh
`openai/gpt-6-luna` run scored 16/20. Keep the same prompt, retrieval budget,
judge, original images and processed store so the answer model is the changed
factor. This is a development comparison, not held-out publication evidence.

The existing `retrieval-ablation --profile full` already validates the live
source, uses the native SDK/P3 loop, freezes experiment identity and preserves
independent resumable answers/grades. Its `answer_model` field is fingerprinted,
but the caller cannot choose it; `_run_answers` currently always uses the
source model. Add an explicit `--answer-model` option for this profile and
send that same recorded model to the provider. Omission inherits the source.
High reasoning, temperature, prompt and all other contracts stay unchanged.

Do not edit the original run's model, fingerprint or answers, hide a model
substitution inside a provider wrapper, or re-ingest to change only the
answering model. A new canonical model/protocol would change the standard
benchmark contract rather than isolate this comparison. A new replay/restore
framework duplicates the existing full-profile experiment path.

Reject the option for the other runtime/access profiles and reject empty or
whitespace-containing identifiers. Resuming a different model must reject the
existing output before paid work. Record source and runner revisions separately;
the old ingestion does not become the new runner's ingestion. Freeze the exact
answer prompt, keep raw tool traces and actual model usage, and archive all
comparison outcomes alongside the existing complete-store backups.

No converter, chunker, extraction, embedding, normalization, label or paid-work
reuse generation changes. No new source processing or image conversions are
needed. Provider charges remain under explicit evaluator and account ceilings;
uncertain calls retain the existing at-least-once accounting contract. Verify
requested-model delivery and fingerprint/resume isolation with controlled
provider tests, then run the complete sample with the frozen original store.

The owner additionally requested parallel answering and a Meta Muse Ember
comparison. Use `--answer-workers` (default one, bounded to 16) for independent
full-profile questions. Each question owns its native trace/state and usage
journal; one collector checkpoints completions. A short shared mutex admits
calls and records known charges, with no provider or retrieval I/O under it.
Bound the number of outstanding questions; on a technical failure stop new
calls, drain already admitted calls, save other completions and interrupted
usage, and preserve the original error. An uncertain in-flight call retains
the existing at-least-once charge behavior. The spend threshold stops future
admission when charges become known; up to the worker count of previously
admitted calls can still finish. It is not an exact provider-dollar limit.

An explicit ordered `--answer-provider` allowlist is needed for non-OpenAI
answer models; freeze it in experiment identity and apply it only to the
answer provider. The canonical judge and original source runtime retain their
OpenAI policy. Omission inherits that source policy. Worker count and provider
changes on resume reject the old output; omitted defaults preserve existing
serialized identities. Do not hide routing changes in environment overrides.

Live OpenRouter catalog/endpoints on 2026-11-11 list `reasoning_effort` and
structured output for both requested models; tiny HIGH probes also succeeded.
Sources: <https://openrouter.ai/api/v1/models>,
<https://openrouter.ai/api/v1/models/meta/muse-ember/endpoints> and
<https://openrouter.ai/meta/muse-ember>. The operational workbook preserves
dated pricing/capability and usage receipts; no model-price claim is built
into engine defaults.
