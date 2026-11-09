# Answer scope and calendar wording in LoCoMo

Non-binding analysis, 2026-11-09. Candidate02 is under a complete development
trial; promotion remains conditional on its result and review.

The current answer policy can overvalue a direct but merely related statement
over concrete clues explaining the requested attribute. It also says to choose
one best interpretation even when a question permits several independent
answers. Finally, resolving dates internally does not ensure a final answer
actually names a calendar date or period instead of another relative phrase.

Use a small prompt-only change: prefer statements answering the requested
attribute, weigh distinctive source clues, match single versus plural answer
scope, and express relative times as explicit calendar periods at supported
precision. Keep source-clue requirements, uncertainty, entity/date fidelity,
models, retrieval depth, tools, judge and budgets. There are no example answers,
people, topics, categories or reference IDs in the added instructions.

Alternatives are retaining the existing wording, changing the model, ranking
retrieval differently, or loosening the judge. The wording change addresses
answer construction directly without changing engine behavior. Every candidate
is run over the whole selected sample, with complete traces/grades/costs saved
independently in GCS through D124's full-surface experiment. Sampling variation
remains a confound; repeated development scores are not held-out results.

The first complete trial stayed at17/20: calendar wording improved one answer,
while another inference became noncommittal. All64 tools succeeded without
typed negatives. Candidate02 adds only the general single/plural scope rule
to that prompt. The original store and all original results remain unchanged.
Evidence is in cloudPR850, experiment locomo-conv47-best-answer-20261109.

This PR stages the exact reviewed candidate02 bytes for possible promotion;
it must remain draft unless that candidate meets the development gate. Changed
prompt/adapter bytes produce a new evaluation identity. The one current
Full-v40 key is retained; no retired execution branch or compatibility shim
is introduced. No ingestion generation, D56 reuse key or backend changes.
A later independent conv-42 test must score20/20 after conv-47 reaches at
least19/20 under the same final template before publication may start.
