# Best-supported answers in the LoCoMo evaluator

Owner-authorized evaluation policy, 2026-11-09. This analysis motivates the
current answer-loop contract in
[`locomo_benchmark_design.md` §7](../designs/locomo_benchmark_design.md).

The pinned dataset has 1,986 questions: 1,540 in scored categories 1–4 and 446
in excluded adversarial category 5. A scan of the actual pinned JSON on
2026-11-09 found no missing answer in categories 1–4 and no gold containing
`Unknown`, `unanswerable`, `not mentioned`, `not specified`, `cannot be determined`,
or `not enough information`. Category 5 has 444 records without an `answer`
field and is not evidence about the scored answer policy. The pin is recorded
in the benchmark design §2; execution still validates its bytes.

The previous prompt explicitly permitted `Unknown`, although the unchanged
judge counts it wrong. In the retained conv-47 run all three misses were
abstentions even though the cited source chunks reached the reader; see
the diagnostic analysis in PR #561. Two questions expect uncertain commonsense
inferences. Another has a question/reference date conflict. Removing abstention
does not make those inferences certain or resolve an annotation conflict.

Alternatives are to retain conservative abstention, change the judge, add
question-specific hints, or ask for the most plausible supported answer with
an uncertainty qualifier. The owner selected the last policy. Full-v40 orders
support as direct statement, conversational implication, then contextual
commonsense inference. It asks for an actual answer even when evidence is
incomplete, and forbids invented supporting details or silently replacing a
question's date. There are no named benchmark people, places, conditions, gold
answers, or reference turn IDs in the prompt. The judge, dataset, models,
retrieval pool, and tool/cost limits stay unchanged.

This can trade abstentions for incorrect guesses. Qualifiers communicate
uncertainty but are not proof. Prompt compliance is not guaranteed by schema:
the existing harness records noncompliant answers and failures honestly and
does not synthesize replacements. No new private retries or model calls are
introduced; cost remains within existing ceilings. Engine typed negatives,
claim/fact semantics, ingestion identities, and source visibility are unaffected.

Validation is a fresh 20-question conv-47 answer/judge run over retained,
contract-compatible ingestion, with original results preserved. Its fresh
prompt hash, evaluation revision, source-ingestion provenance, tool traces,
answers, grades, and verified GCS backups must remain distinguishable from the
previous run. Repeating this development conversation is not evidence of
held-out generalization or a full-benchmark result. No score is claimed here
before execution.
