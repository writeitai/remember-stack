# Complete constraints and candidate references in answer-only trials

Non-binding analysis, 2026-11-11. The owner requested three generic prompt
adjustments after a complete Luna Pro HIGH run repeated the Luna baseline16/20.
The four misses and the historical saved20/20 are recorded in cloud experiment
`experiments/locomo-luna-pro-c42-20261111/historical-comparison.json` (PR948).
The old score combines selective reruns, and its Sol judge differs from the
current Luna judge despite byte-identical primary judge wording. Do not treat
that old score as proof that a fresh run will reach20/20.

The new instructions preserve compatible categories and their defining criteria
instead of reducing constraint deductions to an isolated example; require an
independent claims/source search across credible preference/reference candidates
before all follow-ups narrow to one name; and retain relevant restrictions with
the source's qualifiers instead of omitting or strengthening their category.
They introduce no named answers, people, source labels, medical equivalences,
forced answer pooling or evaluator hints. Existing adjacency, attachment,
calendar, list-completeness and uncertainty rules remain.

Keeping the prompt unchanged reproduced the same misses with a more expensive
reader. Copying the entire historical paragraph would restore named examples
and mandatory multi-answer pooling. These smaller rules separate its useful
retrieval and coverage instructions from those choices. Stronger engine ranking
or new extraction would change more than this owner-authorized answer-only test.

Full-v44 changes evaluation identity only; the judge wording/model, canonical
Luna/Jev role profile,22 tools,8-tool/9-model cap, retrieval budgets and all
D56 ingestion/reuse generations are unchanged. Additional reads/continuations
remain inside those bounds. Incomplete or incorrect answers and provider
failures remain visible; there is no answer repair, hidden reranker or retry.

For the retained Full-v43 store, use its unchanged frozen runner with the
existing `retrieval-ablation --profile full --answer-prompt` override. Export the
exact new template from this source and record its SHA and source revision in
a separate experiment. This avoids changing the old source configuration or
re-ingesting just to try a new reader prompt. The comparison records source
processing revision, actual runner revision and prompt source separately.
Complete all20 questions, save every answer/trace/grade/charge, and verify a
new native complete-store GCS archive. The owner permits skipping external
review for faster iteration; normal offline/CI checks remain required.
