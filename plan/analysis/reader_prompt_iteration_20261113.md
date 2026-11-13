# General reader reasoning: retained-data prompt trials

Non-binding analysis, 2026-11-13. This continues the source/reader fidelity
investigation and the accepted consumption contract. It does not establish an
accuracy improvement. The owner authorized Opus 5.5 HIGH to adjust the answer
prompt, with Codex operating the tests, benchmark runs and infrastructure.

The first full conv-43 replay of PR593 is finishing. Its result cannot improve
on the published 151/178 baseline; all 178 answers were generated without
answer/tool failures or database-budget deadlines. Seven previously diagnosed
reader errors received all cited original dialogue utterances in both runs.
Quote visibility does not by itself prove semantic sufficiency. Detailed frozen
results and raw source evidence live in the cloud benchmark workbooks/GCS, not
in the runtime prompt.

The experiment freezes the completed replay's incorrect item IDs as a development
cohort. A candidate should recover at least half of that cohort with the same
Luna Pro HIGH answerer and judge, judge prompt, retrieval settings and call
budgets. Then regenerate and judge all 178 questions with that exact candidate
prompt; count gains and regressions against the frozen replay. Development-only
results must never be presented as a new full-conversation score or combined
with the best answers from earlier trials.

Opus owns the wording and examples. They must express reusable memory reasoning:
complete relationship-specific lists, matching repeated events, indirect
references, ordinary-language interpretation and qualified correction of a
mistaken premise. The exact choices follow evidence inspection; this is not an
instruction to force a gold label when sources disagree. No conversation IDs,
turn IDs, benchmark names/answers or dataset-specific entities belong in runtime
rules or examples. Generic examples may illustrate the reusable distinction.
Keep the answer schema, tool contracts and budgets unchanged, and keep the
prompt within the existing 18,284-character size guard.

Codex runs and records each trial through the existing native `retrieval-ablation
--answer-prompt` option. It freezes the exact template bytes and SHA256 while
using the already ingested v45 store, its existing P3 projections and the attested
reader-serving revision. The prompt source is committed on latest main; its
commit/hash is recorded separately from the unchanged evaluator/serving source.
No old input is relabelled as v46 and no visual product is regenerated. The
merged visual-label change is outside this retained-data experiment.

Every candidate and coherent result checkpoint is committed and visible in a
PR. Codex runs validation, saves native answers/tool traces/judgments for every
trial to separate immutable output directories and verifies GCS copies. The
original run, baseline answers and processing counters stay unchanged. The
final all-question run is required before claiming that the miss reduction
holds across the conversation.
