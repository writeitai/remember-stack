# T.4 extraction smoke — 2026-09-11 (resolved dates written into claim text)

Twelve synthetic cases passed against the pinned `openai/gpt-5.6-luna`
extractor through the actual OpenRouter adapter and strict ClaimifyResponse
schema, under extractor generation `temporal-anchor-4` (D41/D32 amendments of
2026-09-11). The saved JSON records expected endpoints, kind, precision,
optional claim-text expectations, generated claims, parsed fields, the
verdict of the real D32 grounding gate on each returned claim, and provider
usage. This is a small semantic smoke, not an extraction-accuracy estimate or
a LoCoMo score. The twelve calls reported $0.0060 combined cost.

The eight cases of the 2026-09-06 smoke are reused unchanged and still pass.
Four new cases check the new rule end to end, including the deterministic
gate as E2 applies it:

| Case | Source | Written claim text | Gate |
| --- | --- | --- | --- |
| `last_friday_written_as_day` | "Joanna: I printed the screenplay last Friday." on 2022-01-23 | "Joanna said: Joanna printed the screenplay on 2022-01-21" | accepted |
| `yesterday_inside_attributed_speech` | "Caroline: I went to a support group yesterday." on 2023-05-08 | "Caroline said: I went to a support group on 2023-05-07." | accepted |
| `last_year_written_as_year` | "Melanie: Yeah, I painted that lake sunrise last year!" on 2023-05-08 | "Melanie said: Melanie painted that lake sunrise in 2022" | accepted |
| `few_weeks_stays_as_spoken` | "Joanna: I have been working on the screenplay in the last few weeks." on 2022-01-23 | "Joanna said: I have been working on the screenplay in the last few weeks." with `unknown` precision | accepted |

In every resolved case the model listed the written date in `added_context`
and the gate admitted it only through the claim's own valid-time bounds (the
date occurs nowhere in the source text). The two relative-hour cases now
write the instant exactly as `valid_from_iso`. The first case is the
LoCoMo conv-42 question that the v25 Gemma run answered with the message
date instead of the resolved day.

The probe script now also applies the optional `expected_text_contains` /
`expected_text_excludes` fragments and runs `_grounded_claim` on a synthetic
one-chunk source whose header date is the case timestamp, so a prompt change
that makes the model write a date the gate would reject fails here rather
than in production ledgers.

Reproduce with the standard `REMEMBERSTACK_OPENROUTER_API_KEY` development
credential configured securely, from the repository root:

```sh
uv run python scripts/probe_temporal_extraction.py \
  --cases plan/implementation_evals/temporal_extraction_t4_smoke_20260911.json \
  --output /tmp/temporal-extraction-smoke.json
```

Only synthetic content is submitted. Reruns incur provider usage and can
produce different outputs; a failed case is retained in the output file and
returns a nonzero exit status.
