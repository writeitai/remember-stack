# Fact-adjudication reliability fixes (R8 dead-letter analysis)

Session goal: eliminate the R8 Vertex/Gemma dead-letters that survived PR #411,
then run a fresh Vertex/Gemma processing with all fixes. Status boxes track
execution; check them as each step lands. Reviewer: Grok
(`grok --always-approve --model grok-4.6 -p "<prompt>"`) reviews this plan and
each implementation.

## Census (R8, final, from processing_state)

30 dead-letters, all at 3 exhausted attempts; 12 more units stumbled and
recovered. Zero F-name errors — the #411 fix held.

| Cause | Dead | Mechanism (translator/adaptor site) |
|---|---|---|
| new-handles mismatch | 15 | `translate_prompt_decision` rejects target/declaration mismatch on N-names (core/concise_adjudication.py:555) |
| schema-invalid completion | 11 | JSON fails `PromptFactDecision` validation; never reaches translator |
| incoming-support-as-move | 3 | Incoming assertion filed in `support_moves` instead of via `target` (:594) |
| stream truncation | 1 | 59KB+ repeated-text stream never completes |

## Fix A — prompt discipline for the two translator classes (PR, cheap)

Two sentences in `_FACT_PROMPT` (spine/fact_adjudication.py), next to the #411
wording:

1. Mixed case: "When target is a supplied F-name, new_facts must be empty —
   never declare an N-name you do not target." (Kills the hedging shape behind
   most of the 15: reuse F1 but declare N1 "just in case".)
2. Incoming placement: "The incoming assertion's own placement is decided by
   target/stance alone; never list the incoming A-name in support_moves."
   (Kills the 3.)

Translator stays strict — no safety-net changes. Tests mirror #411: prompt-text
assertions on the new sentences plus the existing example completeness test.
Pin bumps mirror #411 exactly (`git show 6776914c --stat`): new adjudicator
generation suffix in spine versions, `benchmarks/locomo/protocol.py`
generations + fingerprint, protocol/runner tests, README deltas.

- [x] Implement + tests green + lint clean
- [ ] Grok implementation review
- [ ] PR opened, CI green, merged

## Fix B — rejection-aware retry (design, then PR)

Deterministic `temperature=0.0` retries reprint the same wrong answer (one input
reprinted identical bytes 6x). Seam: spine/fact_adjudication.py:225-228, where
the translator `ValueError` becomes `ApplicationInputChanged`.

Design points to settle before code:

- On retry, append the rejection reason to the next attempt's prompt
  ("rejected because <reason>; fix only that"), keeping the template identical.
- Verify `snapshot_hash`/CAS covers inputs, not prompt text, so a per-attempt
  note cannot corrupt claiming; note is deterministic given the attempt chain.
- Scope to translator rejections only (never provider errors); attempts stay
  capped at 3. New generation component suffix for the feedback behavior.
- Tests: unit (note appended iff previous attempt rejected), worker-level
  retry-then-accept path, no-note-on-first-attempt.

- [ ] Design settled + Grok design review
- [ ] Implement + tests + lint
- [ ] Grok implementation review, PR opened, CI green, merged

## Fix C — schema-invalid class (blocked on R12 field data)

R8's engine logged only content hashes, so per-unit causes are unrecoverable.
R12 runs the merged validation diagnostics (#412/#413): its error messages name
failing fields. When R12 reaches adjudication:

- If 1-2 consistent shapes: another prompt example (Fix-A-shaped PR).
- If scattered: fallback-model design (separate decision; needs pinned seat,
  per-call serving receipt, cost accounting).
- Enabler candidate regardless: opt-in raw capture dir for the Vertex adapter
  (parity with the OpenRouter capture dir), mode 0600, no prompts.

- [ ] Collect R12 field-level causes
- [ ] Implement the matching fix + tests, Grok review, PR, merge

## Fix D — stream repetition guard (bounded engine change)

Abort a streaming completion on long repeated spans and surface it as a normal
retryable provider error. Rare (1 unit) so it will never get prompt attention;
adapter-level fix with unit tests on a synthetic repeated stream. No retry,
pricing, or schema changes.

- [ ] Implement + tests + lint, Grok review, PR, merge

## Merge + R13 (Vertex/Gemma)

After all implementable fixes merge (C resolves on R12 data or is explicitly
deferred): fresh R13 root/ports/DB, engine + runner bundles from merged mains,
prepare asserts (29 sessions, Gemma seats, new pins), launch, verify early
calls. R8/R12 stores untouched.

- [ ] All fix PRs merged
- [ ] R13 provisioned, prepared, launched, early calls verified

## Review log

- [ ] Grok plan review (this file)
- [ ] Grok implementation reviews (A, B, C, D)
