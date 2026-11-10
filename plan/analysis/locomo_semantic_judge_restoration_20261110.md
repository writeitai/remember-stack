# Restore the historical branch's semantic judge

Non-binding analysis, 2026-11-10. The owner requests the permissive grading
logic used on the historical high-scoring branch, with fresh conv-42 followed
by conv-47 if conv-42 reaches 20/20. Ingestion uses the newly restored actual
OCR/Luna image inputs; the answering template remains the selected baseline
plus calendar-date clarification.

## Source and actual differences

The old `JUDGE_PROMPT_TEMPLATE` is extracted directly from engine commit
`9c84eacb9ca1b337a71ce7280e4a456e0d9f0dcc`. Its exact UTF-8 SHA256 is
`72a063630a0b8a916d1e9c2c5f5c592e3cad42baa337004a139cd2eb92ff8900`.
It accepts an answer that identifies or encompasses the reference's core
fact, entity, date or event. Synonyms, extra context, and an encompassing
explanation do not make the answer wrong. A genuinely different fact/entity/time,
contradiction or missing/Unknown answer is wrong. Calendar expressions must
be equivalent; it does not specify an arbitrary date-error tolerance.

The shorter recent template, SHA256
`cd4007be1e96c9d6441c2c68aac09c2de8fe662f2be9e3730157aa622ce758ca`,
lacks that explicit core-fact acceptance rule. A saved conv-42 prediction was
accepted under the old grader and rejected under the recent one, but both the
model and template differed, so that observation does not isolate a cause.
Historical grading used Sol high. The owner-selected current model remains
Luna high; restoring the template does not reproduce the entire old experiment.

The independent `judge_sidecar.py` preserves a separately named Mem0-like
policy and a pinned upstream URL. That sidecar additionally tolerates numeric
and list differences and can optionally inspect source evidence. Its policy
and outputs remain separate. This restoration uses the exact verified branch
template as the primary scorer, without claiming an exact Mem0 score reproduction.
No source/gold reference errors are silently corrected in old records.

## Decision, alternatives, and boundaries

Use the exact old-branch template, unchanged, under a new current protocol
identity. This directly implements the owner's requested policy and keeps the
acceptance rule general across all questions. The alternative of retaining
the recent shorter rubric does not implement the requested branch behavior.
Switching the primary grader wholesale to the independent sidecar would
introduce additional list, date, duration and evidence tolerances beyond this
verified branch template.

Keep Luna high for answering/judging, Luna low for ingestion/vision, Jev for
adjudication, provider order, retrieval depths, schemas, call and spend limits,
and the selected answer template. Judge prompt tokens change and their actual
usage is recorded within the existing evaluator ledger; there is no extra
judge retry, majority vote or budget increase. There is no pipeline, reuse-key,
converter, chunker, embedding, extraction or adjudication change. Fresh image
input already requires fresh ingestion; grader restoration alone does not.

Prepare a new Full-v42 identity containing the restored judge hash. Saved old
runs keep their answers, judgments, source files, metadata and hashes. Retired
protocols stay read-only rather than becoming executable fallback paths.
Validate with existing protocol/runner/sidecar tests, type and docs checks,
then the requested Antigravity review and normal CI/merge. The fresh run must
freeze actual image input, both prompts and actual model/provider metadata,
and use verified native scoring-base/final GCS backups.

The next validation order is conv-42 first; only if it reaches 20/20, run
conv-47 and require at least 18/20. Full publication remains conditional on
both fresh gates, healthy ingestion/retrieval, verified backups and budget.
A score under the restored rubric is labeled with that policy; it is not a
one-variable comparison against the preceding run that also lacked image input.
