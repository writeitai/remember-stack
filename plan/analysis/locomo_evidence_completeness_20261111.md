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

## Follow-up: the two remaining misses

The first full trial of template SHA256
`6aa04788622c012ae868962e5abbc24bc552cf01eaeaa74954b176e6fad755a4`
scored18/20 with Luna Pro HIGH and the unchanged Luna HIGH judge, with no
technical failures. Its saved source, answers, grades and traces remain frozen
in cloud experiment `experiments/locomo-completeness-c42-20261111/` (PR950).
This is a development trial, not held-out validation. The following audit
justifies two small follow-up instructions, not a claim that they improve scores.

### Image-derived book evidence: excluded before ranking

The current rendered D9 source retains Nate's recommendation, an owning-turn
label, native OCR/Luna visual reading and the visual observation that the book
covers feature dragons. The corresponding extracted claim
`3a3f1b62-ee7e-46b0-9093-2bd707a1a7a0` has **no resolved entity mentions**.
The two source chunks around the attachment also have no resolved Nate mention:
the first has none; the second has only the top and bottom rows of books.
Textual speaker labels do not by themselves create resolved entity links.

The reader resolved Nate and kept his entity ID on both facts and claims/source
queries. In `src/rememberstack/adapters/postgres_p1.py`'s
`nominate_testimony_scored`, scoped claim/chunk candidates join
resolved mentions before nomination. `query_engine.py` also retains that scope
during hydration. Therefore the relevant image claim and chunks are ineligible
for these reads, regardless of a larger candidate pool or later reranking.
History-mode facts retain their anchor scope rather than expanding the graph.

Six read-only public API probes on the unchanged processed store usedk50 and
candidate_k200, history mode and the same API build. Neither expected answer
words nor source-turn IDs were supplied in the search queries. Results:

| Query | Nate entity scope | D9:14 source/dragon detail returned |
| --- | --- | --- |
| Actual reader facts query | Present | No |
| Same facts query | Omitted | No |
| Actual reader claims/source query | Present | No |
| Same claims/source query | Omitted | Yes, as a chunk |
| Generic book/recommendation class query | Present | No |
| Same generic class query | Omitted | Yes, as both claim and chunk |

The generic query was "Book series people read, love or recommend; descriptions
of the books and what they are about". The exact prior source query included
the first candidate's praise, which narrowed its focus, but removing just the
entity filter still recovered the needed chunk. Raw responses, SQL evidence,
source hashes and before/after counts are retained in cloud experiment
`experiments/locomo-image-retrieval-audit-20261111/` (PR954).
All2732 processing rows,5330 ingestion ledger entries and USD0.848937 ingestion
cost stayed unchanged; no ingestion, image conversion, answering or judging ran.
Retrieval embeddings have separate metering.

The old saved20/20 trace already received a person-linked claim that Joanna
thanked Nate for recommending a fantasy series centered on dragons. The current
store instead has a standalone visual observation without that association.
This establishes a representation/link difference, but does not isolate which
ingestion change caused it. The current source is still sufficient when read
without the semantic entity filter. A future ingestion investigation would
need to distinguish attachment ownership, reference resolution and extraction
selection; this patch changes none of those paid stages or their D56 identities.

Chosen correction: when a required detail remains unresolved after scoped reads,
use the existing claims/source tool without `entity_ids` for the missing detail
or object class, then verify ownership in the source and adjacent chunks.
Explicit API filters must retain their declared meaning; silently widening them
would surprise callers. Automatically attributing every pictured object to the
speaker would also fabricate relationships. A reranker or larger pool cannot
repair exclusion at eligibility. The conditional reader search costs an extra
read and possible embedding/continuation within the existing eight-tool cap;
unrelated hits remain a risk and require source attribution checks. Deployment,
visibility and time constraints are unchanged.

### Allergy answer: an unsupported denial after sufficient retrieval

The reader received the fur/reptile/cockroach evidence, a lactose-intolerance
statement and the source restriction on dairy. Its answer retained those values
but added "not allergic to dairy". The delivered evidence establishes no such
negative. The benchmark uses allergy terminology more loosely than the source;
preserving the source's qualification can express that distinction without
claiming a condition is absent. The old accepted answer did so parenthetically.

Chosen correction: a source category neither proves nor excludes a different
category. Preserve its qualifiers, and require evidence or valid logical
exclusion for a negative claim. This is symmetric grounding, not a medical
equivalence or a named-answer example. Judge wording, model and saved grades
remain unchanged. No new answer/score run is included in this analysis task.

These follow-up bytes receive their own template hash. Full-v44 is still an
unmerged protocol implementation; the earlier explicit-template trial retains
its original hash and must never be relabelled as a trial of the final bytes.
