# LoCoMo counterfactual answer analysis

**Status:** non-binding analysis, 2026-09-01  
**Evidence:** completed `RS-LoCoMo-Full-v18` conv-26 smoke run at repository
revision `e7b173a19e8a992ec57bf75ce6593373ab2fc2c5`

## Finding

The eight-question conv-26 smoke run scored 7/8. Its only miss was category 3
item `conv-26/qa/0014`:

| Field | Value |
| --- | --- |
| Question | Would Caroline still want to pursue counseling as a career if she hadn't received support growing up? |
| Gold answer | `Likely no` |
| Generated answer | `Unknown` |
| Retrieval | one successful `answer_context` call; 61 claims returned |

The retrieved response contained the load-bearing direction. Among its claims
were that support made a huge difference to Caroline, counseling improved her
life, she then cared more about mental health, and she wanted to help others
through the same journey. The gold evidence points to the same source turns.
The answer agent therefore did not lack evidence or need a deeper traversal.
It declined to convert an explicit motivational chain into the corresponding
counterfactual answer.

## Cause

Full-v18 said both “Never use outside knowledge” and “If the deployment does
not contain the answer, finish with `Unknown`.” It did not distinguish outside
knowledge from a bounded inference over causal or motivational relationships
that the deployment did contain. Because the source never stated the
hypothetical sentence verbatim, the zero-effort answer model chose the
conservative escape hatch.

The mechanical content-before-`Unknown` guard behaved as designed. It only
rejects `Unknown` after identity-only or metadata-only reads. This item had
already completed a content-bearing `answer_context` call, so the guard had no
basis to reject the terminal answer.

## Smallest change worth testing

Tell the answer agent that hypothetical and counterfactual questions may be
answered from causal or motivational relationships in retrieved evidence even
when the source does not state the hypothetical verbatim. The instruction
should map a removed condition that caused, enabled, or motivated the outcome
to `Likely no`, map evidence of independence to `Likely yes`, and reserve
`Unknown` for evidence that gives no direction about the dependency.

This is a prompt-only change. It adds no retrieval call, model call, retry,
reasoning effort, category router, or benchmark-item special case. It must roll
the frozen protocol identity because the answer prompt and fingerprint change.

## Alternatives not chosen

- Retrieve more evidence: rejected because the first response already
  contained the complete causal chain.
- Add a special category-3 tool route or item-specific hint: rejected because
  it would encode benchmark structure or gold-derived behavior instead of a
  general reasoning contract.
- Increase reasoning effort for every question: rejected as a corpus-wide cost
  increase before testing the narrow prompt failure.
- Mechanically retry every evidence-backed `Unknown`: deferred. It adds calls
  and requires the harness to classify subjective evidence sufficiency. The
  existing guard should remain objective and simple unless the prompt-only
  replay shows that it is insufficient.

## Validation boundary

The retained Full-v18 conv-26 store can be reused because this change does not
alter document bytes, ingestion, facts, graph state, or P3 content. A valid
comparison creates a fresh Full-v19 run identity, carries forward only the
matching immutable ingest records for that retained deployment, and reruns all
eight answers and judges. It does not copy Full-v18 answers, judge records, or
evaluator spend. A score improvement is measured evidence, not guaranteed by
this analysis.
