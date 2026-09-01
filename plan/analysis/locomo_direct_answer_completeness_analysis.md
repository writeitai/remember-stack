# LoCoMo direct-answer completeness analysis

**Status:** non-binding analysis, 2026-09-01  
**Evidence:** completed `RS-LoCoMo-Full-v19` conv-26 answer-and-judge replay at
repository revision `fd6fb6514425172fe00e58e98570d17ce21895ad`

## Finding

Full-v19 fixed the intended counterfactual item: `conv-26/qa/0014` changed from
`Unknown` to the exact gold answer `Likely no`. The eight-question score still
remained 7/8 because a different item regressed:

| Field | Value |
| --- | --- |
| Item | `conv-26/qa/0003` |
| Question | What did Caroline research? |
| Gold answer | `Adoption agencies` |
| Full-v18 answer | `Counseling, mental health careers, and adoption agencies` |
| Full-v19 answer | `Counseling and mental health career options` |

The Full-v19 answer used one successful `testimony_context` call. Its 50 claims
included both the adoption-advice claim and the explicit statement that
Caroline was looking into an adoption agency, at ranks 22 and 23. Retrieval was
therefore sufficient. The model selected two other directly related research
topics and stopped before a third direct answer.

## Smallest response

Add one general completeness rule to the frozen answer prompt: when retrieved
evidence supports multiple distinct values that directly satisfy the question,
include all of them rather than stopping at the first or highest-ranked match.
Exclude merely related facts that do not satisfy the requested action or
relationship.

This keeps the existing shortest-complete-answer rule but removes an ambiguity:
“shortest” does not mean “incomplete.” It adds no retrieval call, retry, model
effort, category route, or item-specific term. The answer prompt and immutable
protocol identity must roll.

## Alternatives not chosen

- Force `answer_context`: rejected because `testimony_context` already returned
  the direct evidence and is the semantically correct cheaper path for what a
  source said.
- Increase retrieval depth: rejected because the missing value was already at
  rank 23 in the first response.
- Retry incomplete-looking lists: rejected because the harness cannot identify
  semantic incompleteness objectively without another model call.
- Mention research or adoption in the prompt: rejected as benchmark-item
  overfitting rather than a product answer contract.

## Validation boundary

A Full-v20 replay may reuse the same immutable 19 ingest records and retained
store, while resetting readiness, answers, judges, usages, and evaluator spend.
The result is measured over all eight items; neither this analysis nor one
successful target answer guarantees a perfect aggregate.
