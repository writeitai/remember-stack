# T.4 extraction smoke — 2026-09-06

Eight synthetic cases passed against the pinned `openai/gpt-5.6-luna`
extractor through the actual OpenRouter adapter and strict ClaimifyResponse
schema. The saved JSON records both expected endpoints, kind, precision,
source timestamps, generated claims, parsed fields and provider usage.
This is a small semantic smoke, not an extraction-accuracy estimate or a
LoCoMo score. The successful eight calls reported $0.0055486 combined cost.

Cases cover open CEO tenure, a bounded employment period, calendar-fiscal
revenue, the same relative-hour wording in two same-day sources, unresolved
part-of-day timing, missing relative anchors, and explicit dates without a
header timestamp. Unknown timing must preserve the source claim.

The first live probe found an Azure rejection of `description` beside an
enum `$ref`; the strict-schema adapter now keeps that description outside a
one-branch `anyOf`. A following probe omitted the claim with unrepresentable
part-of-day time; the prompt now explicitly keeps such claims with unknown
fields. The final recorded response set passes all eight cases, including
both endpoint checks. Those findings would not have been detected by parsing
canned model outputs alone.

Reproduce with the standard `REMEMBERSTACK_OPENROUTER_API_KEY` development
credential configured securely, from the repository root:

```sh
uv run python scripts/probe_temporal_extraction.py \
  --cases plan/implementation_evals/temporal_extraction_t4_smoke_20260906.json \
  --output /tmp/temporal-extraction-smoke.json
```

Only synthetic content is submitted. Reruns incur provider usage and can
produce different outputs; a failed case is retained in the output file and
returns a nonzero exit status.
