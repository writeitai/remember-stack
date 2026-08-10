# Question turns and claim extraction

**Status:** non-binding current-state analysis and proposed correction, 2026-08-10

**Code inspected:** `main` at `c6f263c9`

**Binding design inspected:** `plan/designs/e2_e3_claims_relations_design.md`

## Executive answer

RememberStack is **less exposed than the reported one-shot implementation**, but
it is not yet impossible for the same defect class to occur.

The intended rule is already present in the binding E2 design and the running
Selection prompt: questions are dropped rather than treated as factual claims.
The provider schema has an exact `drop_question` outcome, the loss ledger can
record `question`, and a claim anchored only in a dropped question range is
rejected unless another kept range overlaps it. Those are meaningful but
conditional protections.

The remaining hole is semantic enforcement. There are two paths. First,
Selection can incorrectly return `keep` or `keep_flagged` for a question and a
question-anchored declarative claim can pass the deterministic gates. Second,
Selection can correctly drop the question, but the fused call still sees the
full bundle and can attach the question's broader proposition to a narrow
answer span. Current grounding proves source location and token membership; it
does not prove that the answer endorses the resulting proposition. The in-call
`entailment_self_verdict` does not close either path because it is stored as an
advisory value, including when false.

The correction can remain narrow. Do not add a dialogue subsystem or a general
natural-language parser. Tighten the E2 contract and prompts, add a conservative
question-anchor rejection for the direct path, and use a selectively triggered
answer-authority entailment gate for claims produced in question-bearing
context. Prove both paths on a six-case dialogue golden set. Questions that
matter operationally can be a separate future object, but they must not enter
`claims` or `facts` as if their propositions were true.

## 1. The contract dialogue needs

In dialogue, the answering turn is the authority for an answer-derived claim.
The question may supply the proposition needed to make a short answer stand
alone, but asking a question is not evidence that its proposition is true.

| Source shape | Claim behavior | Evidence anchor |
| --- | --- | --- |
| Question with no answer | Emit no factual claim | None |
| Question followed by an unqualified “yes” | Emit the affirmed proposition | The answering turn; question is context only |
| Question followed by “no” | Emit the negated proposition | The answering turn; question is context only |
| Qualified or partial answer | Emit only what the answer actually supports, preserving every qualification | The answering turn |
| Ambiguous response, hedging without commitment, or topic change | Emit no answer-derived factual claim | None |
| Declarative answer that is narrower than the question | Emit only the narrower answer proposition | The declarative answer |

This is the transcript adaptation of Claimify rather than a change to its
meaning. The original method processes answer sentences in a question-answer
pair: the question is part of the context used to interpret the answer, while
the answer sentence is the extraction target. It also declines when context
does not support one confident interpretation. See
`plan/analysis/claimify_research/questions/C1_claimify_method_spec.md` §§1,
2.1, and 2.3.

A synthetic attributed event such as “A participant asked whether the venue
was open” is a different proposition: it records that someone asked something,
not that the embedded proposition is true. This analysis does not propose
adding that event type. The minimum safe behavior is to drop question turns
from factual claim extraction.

## 2. What current `main` already gets right

### 2.1 The accepted design says to drop questions

The binding E2 design defines Selection as the verifiability gate and includes
questions in the drop set. It separately requires ambiguous candidates to be
dropped rather than guessed. See
`plan/designs/e2_e3_claims_relations_design.md` §3.2, especially lines 73–95.

This is not merely historical prose. The live Selection prompt says:

> Drop unattributed opinions, advice, hypotheticals, generic truisms, questions,
> section intros/conclusions, and “we don't know” statements.

See `src/rememberstack/workers/e2.py:134-148`.

### 2.2 The model has a precise typed outcome

`SelectionDropReason.QUESTION` and `SelectionOutcome.DROP_QUESTION` exist in
`src/rememberstack/model/claims.py:25-70`. The flat outcome enum prevents a
provider from returning a contradictory pair such as “keep” plus reason
“question.” The PostgreSQL vocabulary and loss ledger retain the decision.

`src/tests/test_port_model_values.py:95-122` proves the strict wire value maps
to a dropped question and rejects prose-contaminated variants. This is useful
schema protection, although it does not test whether the model classifies a
real dialogue correctly.

### 2.3 Question drops are enforced against disjoint kept ranges

Only Selection candidates whose verdict is not `DROP` are sent to the fused
decontextualize/decompose call (`src/rememberstack/workers/e2.py:400-425`). A
returned claim must then anchor inside the target chunk and overlap a range
that Selection kept (`src/rememberstack/workers/e2.py:516-559`). A later call
cannot quote a span that occurs only in a dropped range.

This is the strongest difference from a one-shot extractor. When Selection
does the right thing and no broader kept range overlaps the dropped question,
the question is out of the claim path.

### 2.4 The context bundle can support short answers

The fused call receives the target chunk plus the same-section neighboring
chunks and source-derived header/location elements. It can therefore use a
nearby question to decontextualize a target answer such as “yes” without using
outside knowledge. Its prompt also says to omit a candidate when a careful
reader could not select one interpretation. See
`src/rememberstack/workers/e2.py:150-165` and the bundle contract in
`plan/designs/e2_e3_claims_relations_design.md` §§3.1–3.2.

When a question is in the preceding chunk and its answer is in the target
chunk, the existing anchor rule is helpful: the final claim must anchor inside
the answer's target chunk, not the neighboring question.

## 3. The remaining exposure

### 3.1 Question classification is still a model judgment

There is no deterministic question-specific grounding gate. Current gates are
only `span_not_found`, `outside_kept_ranges`, and
`added_context_unverified` (`src/rememberstack/workers/e2.py:496-501`).

Suppose Selection mistakenly keeps this synthetic span:

> Did every support region adopt the new Saturday schedule?

The second call can return “Every support region adopted the new Saturday
schedule,” anchored to that exact question. The span exists, it overlaps a kept
range, and the content words are source-bounded. The deterministic checks
cannot distinguish “the speaker asked whether P” from “the source establishes
P.”

The strict `drop_question` enum guarantees only that a *chosen* question-drop
is well formed. It cannot force the model to choose it. Range-overlap
enforcement also means a dropped question could overlap a separate, overly
broad kept span; the later grounding gate accepts overlap with *any* kept
range. Prompt discipline makes that unlikely but not structurally impossible.

### 3.2 A correct question drop still does not prove answer entailment

The fused call receives the complete bundle, not a redacted bundle with
Selection drops removed (`src/rememberstack/workers/e2.py:416-425`). After a
question is correctly dropped and its narrower answer is kept, the fused model
can still return the question's universal proposition while using the answer
as `source_span`.

That candidate passes the current deterministic layers when its answer span is
real and overlaps the kept answer. Grounding checks only the additions the
model declares; `added_context` may be empty, and no deterministic comparison
asks whether `claim_text` is semantically entailed by the answer
(`src/rememberstack/workers/e2.py:545-600` and
`src/rememberstack/model/claims.py:154-169`). This path requires no Selection
mistake and is not caught by rejecting question anchors.

### 3.3 The answer-authority rule is not explicit

The fused prompt explains coreference, ambiguity, decomposition, attribution,
and grounding, but it does not state the dialogue rules in §1. In particular,
it does not say:

- a question is context, never factual evidence for its embedded proposition;
- a yes/no claim must anchor to the answering turn;
- “no” negates rather than affirms the question;
- a qualified answer must retain its qualification; or
- a topic change supports no answer to the question.

The model may infer these ordinary conversational rules, but they are not part
of the enforced extractor contract.

### 3.4 Current tests prove vocabulary and mechanics, not dialogue semantics

The closest question test feeds an already-correct `drop_question` payload and
checks enum mapping. The E2 chain test covers verifiable statements, an
attributed stance, advice, grounding invention, and resurrection of a dropped
advice span, but not a question-answer exchange
(`src/tests/workers/test_e2_chain.py`).

The existing attribution-scaffolding test proves that a declarative response
following an agreement marker can cross a speaker-label colon. It does not
exercise a bare “yes,” “no,” qualified answer, or topic shift
(`src/tests/workers/test_claimify_loss_ledger.py:225-241`).

There is therefore no checked-in evidence that the configured extraction model
obeys the dialogue contract on realistic turns.

### 3.5 The self-verdict is not a backstop

The fused response includes `entailment_self_verdict`, but `_grounded_claim`
copies it into `ClaimRecord` without rejecting false values
(`src/rememberstack/workers/e2.py:581-600`). This matches the current D32 choice
to treat self-grading as advisory and use an independent sampled audit
(`plan/designs/e2_e3_claims_relations_design.md` §3.3).

This is worth knowing, but it is not the primary question fix: a model that
mistakes a question for evidence may confidently return `true` anyway. Changing
the general self-verdict policy would be a separate design amendment and should
not be bundled without evidence.

## 4. A synthetic narrower-answer example on current `main`

For a target chunk containing both:

> Did every support region adopt the new Saturday schedule?

and:

> The Prague and Vienna teams now cover Saturdays.

the intended current flow is:

1. Selection returns `drop_question` for the interrogative proposition.
2. Selection keeps the respondent's declarative answer.
3. The fused call sees only that kept answer as its candidate, while retaining
   the full bundle for interpretation.
4. It emits the supported proposition “The Prague and Vienna teams cover
   Saturdays,” anchored to the answer.

It must not emit “Every support region adopted the new Saturday schedule.” The
answer names only two teams and does not establish the universal proposition
embedded in the question.

Current code produces this healthy result only if Selection classifies the
question correctly **and** the fused call respects answer authority. The
unhealthy universal claim can land either with a question anchor after a
Selection mistake, or with the declarative answer as its anchor even after a
correct question drop.

## 5. Minimal proposed correction

These are proposals pending owner acceptance; they do not amend the binding
design by themselves.

### 5.1 Clarify the existing E2 contract and both prompts

Owner acceptance must amend the binding design's Selection contract in §3.2,
the grounding acceptance layers in §3.3, and the D33 ledger vocabulary in
§3.4. The corresponding D31–D33 decision text must be reconciled before
implementation. The E2 prompts should then carry the dialogue rules from §1.
The operative language should be direct:

- A question is context only. Never turn its embedded proposition into a
  factual claim.
- An identified interrogative always gets `drop_question`; D35's
  `keep_flagged` fallback for uncertain verifiability does not make the
  proposition embedded in a question eligible for factual extraction.
- When a response answers a question, the `source_span` must be in the
  answering turn, not the question.
- “Yes” affirms, “no” negates, and qualifications must be preserved exactly.
- If the answer is ambiguous, non-committal, narrower than the question, or a
  topic change, emit only the proposition directly supported by the answer—or
  emit none.

This is a clarification of the existing “drop questions” and “do not guess”
decisions, not a new extraction architecture.

### 5.2 Add one narrow deterministic defence for direct anchors

Add a `question_anchor` grounding rejection for a claim whose anchor lies
inside an obvious interrogative source sentence. Use this exact conservative
first boundary rather than the model-returned substring:

- hard boundaries are chunk start/end, CR/LF, ASCII period/exclamation
  (`U+002E`, `U+0021`), ideographic full stop (`U+3002`), fullwidth exclamation
  (`U+FF01`), and the supported question terminators below;
- question terminators are ASCII (`U+003F`), Arabic (`U+061F`), fullwidth
  (`U+FF1F`), Greek (`U+037E`), and Ethiopic (`U+1367`); trailing quote or
  bracket punctuation after a terminator does not change its class; and
- split the target chunk on those boundaries and reject an anchor if any
  source segment it overlaps terminates with a supported question terminator.

This catches a model-returned substring that omits `?` and a superstring that
spans both a question and an answer. Sentence scope also means a declarative
sentence and a following question in one speaker turn do not poison the
declarative anchor. Language-specific interrogative marks outside the exact set
remain a prompt/selective-entailment limitation and are not claimed closed.
Record the rejection in the existing loss ledger like other grounding failures.

Do not build a general multilingual dialogue parser. Punctuationless transcript
questions still rely on Selection and the clarified prompt; the deterministic
guard is defence in depth for the common, high-confidence case. A short answer
such as “yes” remains a valid anchor because the question supplies context but
is not the anchor.

This guard intentionally favors omission over asserting an unendorsed
proposition. If preserving the synthetic event “a participant asked P” becomes
a real product requirement, it should receive an explicit
attributed-question representation rather than weakening the factual-claim
gate.

The guard closes only the direct question-anchor path. It cannot decide whether
a declarative answer entails a broader claim.

### 5.3 Enforce answer authority only where question context is involved

For claims emitted from a bundle containing an explicit question, add one
batched, independent answer-entailment decision before accepting the chunk's
claims. Give that check the candidate claim, its exact non-question source
anchor, the nearby question marked **context-only**, and any relevant
neighboring answer text. Its contract is not “does question plus answer mention
the claim?” but “does the non-question evidence commit to this claim, using the
question only to interpret the response?” Reject failures with an
`answer_entailment_failed` ledger gate.

This check must cover bare yes/no responses, negation, qualifications, narrower
answers, and topic changes. It may be triggered conservatively by a Selection
`question` drop or an explicit interrogative in the target/same-section
neighbors. Punctuationless questions remain a prompt-and-evaluation limitation
unless transcript conversion later provides a typed dialogue role; this
proposal does not add one.

Batch the candidates once per affected chunk and do not run the extra judge for
ordinary prose without question context. This is the smallest honest semantic
enforcement boundary for the answer-anchored path. Token membership, declared
`added_context`, a terminal-question-mark heuristic, and the same model's
self-verdict cannot prove conversational endorsement.

This selectively promotes D32's independent audit from sampled measurement to
a blocking check only on the question-bearing risk class. It adds one model
round trip to affected chunks, not to the whole corpus. A provider failure must
leave the E2 work item retryable rather than accept unchecked claims; the judge
model, prompt, and schema belong in the extractor generation and replay
contract.

If the owner chooses prompt-and-audit only rather than this selective gate, the
result should be described as risk reduction, not closure.

### 5.4 Add a six-case golden matrix

Use one compact dialogue fixture set:

1. unanswered question → zero claims and a `question` drop decision;
2. question + “yes” → affirmed claim anchored to the answer;
3. question + “no” → negated claim anchored to the answer;
4. question + qualified answer → qualification preserved;
5. question + ambiguous response/topic change → no question-derived claim;
6. the synthetic regional-support exchange → exactly the two-team,
   answer-backed claim, with no universal question-derived claim.

There are two distinct proofs:

- deterministic fake-provider tests should show that a question-only dropped
  range cannot resurrect through a disjoint keep, and that an interrogative
  anchor is rejected even when a canned provider marks it `keep`. For case 6,
  a second adversarial payload must correctly drop the question, keep the
  narrow answer, return the universal claim anchored to that answer, and prove
  the answer-entailment gate rejects it; and
- a small, pinned extraction-model evaluation should show that the actual
  Selection and fused prompts produce the expected outcomes across the six
  exchanges, including when question and answer straddle one adjacent,
  same-section chunk boundary (the current bundle is only ±1 chunk).

The latter is the only test that measures semantic classification. It is a
small targeted evaluation, not a full benchmark run.

### 5.5 Version and observe the change

A prompt or grounding-policy change must bump `E2_EXTRACTOR_VERSION`; otherwise
old and corrected claims become indistinguishable in lifecycle and evaluation
records. Track question drops, `question_anchor`, and
`answer_entailment_failed` rejections through the existing loss ledger. A
dialogue golden failure should have zero tolerance because the failure changes
an unasserted proposition into purported truth.

## 6. What not to add

- Do not create a second claim-extraction pipeline for chat.
- Do not add a broad dialogue-act taxonomy.
- Do not treat every sentence ending in `?` as a new fact type.
- Do not introduce an open-question or decision register until a retrieval or
  workflow requirement actually needs one.
- Do not rely on downstream E3 adjudication to repair the issue. Once an
  unendorsed question proposition enters `claims`, later corroboration logic is
  operating on poisoned testimony.

## 7. Conclusion

RememberStack already carries the right high-level rule and has better loss
accounting and two-stage enforcement than a one-shot path. It is therefore
inaccurate to say that current E2 simply treats questions as facts.

It is also too strong to say the defect is impossible. The current safety
boundary neither independently rejects a question used as factual evidence nor
proves that an answer entails a claim derived with question context, and the
dialogue cases are untested. Prompt clarification and the narrow anchor gate
reduce risk; the selectively triggered answer-entailment gate is required to
enforce both paths without broadening E2 into a dialogue subsystem.
