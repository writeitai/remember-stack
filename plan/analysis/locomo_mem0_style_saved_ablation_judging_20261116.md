# Mem0-style grading over frozen native and retrieval-ablation answers

Status: non-binding implementation analysis, November16. The owner requested a
full retained-ingestion answer/native-judge rerun, followed by separately named
Mem0-style grading over those same answers. This extends the already accepted
independent-sidecar contract in the [benchmark design](../designs/locomo_benchmark_design.md#21-model-roles-and-independent-judging).

The existing `judge_sidecar` reads `run/state.json`. The current answer-only
operator instead writes `answers/ablation.json` and a `state.json` envelope.
The retained source run has fingerprint A; the answer execution has fingerprint
B. Both the outer saved envelope and its nested run state use B, while execution
metadata explicitly links back to A. Pretending the nested state has A would
lose provenance. Re-ingesting or generating new answers to fit an old parser
would violate the requested experiment and spend unnecessarily.

Use one current module, `mem0_style_judge`, with explicit `--answers` for the
saved execution. Parse only stable source/answer identity fields, not an old
trace model; hash the complete untouched bytes. Validate A/B relationships,
manifest membership, unique selection and question/reference text before paid
calls. Do not grade an incomplete declared execution. Classic saved runs remain
an actively supported input layout, not an obsolete execution fallback. The
new module replaces the old module name; update callers and shipped docs.

Keep model roles distinct from policy: permit an explicit judge model so the
experiment can use Luna Pro HIGH for both native and alternative judging.
HIGH remains set on every call. Follow the accepted Flex-preferred/OpenAI-normal
fallback pair, and record both routing lists in the independent identity.
The Mem0-inspired policy is unchanged: one matching list item, broad paraphrase,
fourteen-day dates and fifty-percent durations, with separately identified
optional evidence acceptance. Upstream source remains pinned to mem0's commit
4b61c5d31b9c668a12b4f5e78064248a02c82d2b, prompts.py. This is a style comparison,
not a reproduction of a vendor's full proprietary benchmark setup.

Reject output overlapping either source directory in either direction. Retain
in-flight/usage accounting, checkpoint each label, and require reconciliation
for uncertain paid attempts. Preserve original source bytes; a file change
during grading fails before publishing a completed summary. Completed labels
resume without resampling; a WRONG label is a result, not a retry trigger.
Explicit unbilled capacity failures keep their existing technical-retry record.

Costs are one additional judge call per available answer, under explicit call
and reported-cost ceilings and the provider account's hard cap. No ingestion,
retrieval, embedding, extraction, projection or cache identity changes. No
answer regeneration or canonical judge mutation. The full campaign's independent
score is generated only after the native run is complete and backed up.
