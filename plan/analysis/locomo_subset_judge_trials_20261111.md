# Reproducible small answer trials with an explicit judge model

Non-binding analysis, 2026-11-11. The owner authorized iterating on two failed
development questions, using Luna Pro HIGH for judging, then re-running all20
questions after both pass. Every candidate and result must be committed and
visible in a PR. Answer prompts must remain general: no named answers, personal
conditions, source labels or selected item IDs. Question selection belongs only
in the evaluation harness, and all trials remain development results.

The existing full-profile comparison fingerprints a prompt override, reader
model and worker count but always selects the entire sample and uses the
canonical judge model at runtime. Add repeatable `--item` selection and an
explicit `--judge-model`. The selected canonical-order IDs and judge model are
already immutable configuration fields; bind their effective values and reject
changed identities on resume. Reject duplicate/unknown selections before model
calls. Omission still means the whole sample. Keep judge prompt/schema, HIGH
effort, admission budgets and per-question source access unchanged. A subset
does not reduce the searchable evidence corpus or expose reference answers.

There is a separate compatibility defect: current full comparisons reject a
historical source name even when all non-prompt models, ingestion generations,
provider policy, budgets and schemas agree. Full comparisons must first enforce
the existing non-prompt contract against the current protocol, then verify
readiness using that exact matching current contract. This retains the source
processing revision, immutable source fingerprints, exact live document set,
stage/model bindings, query surface and P3 attestation. It does not reintroduce
old executable protocols or admit genuinely different ingestion settings.
Native canonical ingestion/answer commands retain their current-version gates.
Full-profile provider construction uses the same current policy that its
pre-call compatibility guard requires, avoiding an unnecessary old-name lookup.

Editing the old source manifest would conceal identity drift. Copying and
patching an old runner would require a second executable version and extra
provenance. Neither is needed when contracts actually match. Changing the global
judge would also confound default results; an explicit fingerprinted override
keeps its role visible and leaves the current canonical judge untouched.

Paid costs depend on actual returned usage. Two-question trials reduce search,
reader and judge work without changing an individual question's budget. All
outputs, failures, traces, model roles and charges remain durable. A passing
subset is a trigger for the owner's complete20-question development regression,
not a reported full-conversation score or evidence of held-out generalization.
All ingestion/D56 paid-reuse identities remain unchanged. This work does not
introduce a reranker, source rewriting or answer repair. External model reviews
are waived by the owner for faster iteration; meaningful offline checks remain.
