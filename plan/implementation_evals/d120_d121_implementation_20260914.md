# D120/D121 implementation: clear prompts and concise adjudication inputs

**Date:** 2026-09-14
**Lane:** `feat/concise-adjudication-inputs` at
`/Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication`
**Design:** [D120](../designs/processing_prompt_clarity_design.md),
[D121](../designs/concise_adjudication_inputs_design.md), commit `4a803c1d`
(PR #401, merged).
**PR:** https://github.com/writeitai/remember-stack/pull/402 (draft)
**Final integration:** rebased onto D119 `d808164b`, including the origin
evidence-coordinate fixes, final 500-version test and Full-v29 naming. This
branch is Full-v30. The parent owns final prompt wording, integration and merge.
The earlier checkpoint history below is retained as measurement/review evidence;
it does not describe the current dependency state.

Extractor and normalizer use `assertion-clarity-3`; adjudicators use
`concise-handles-5`. Final integration passed 63 concise/protocol checks, the
regenerated serialized-summary check, and 11 PostgreSQL E2/concise-adjudication
checks. No fact-application or locking logic changed in this final integration.
Antigravity's integrated prompt review at `ecedaabe` approved with nits, all
addressed below. The remaining integration delta is limited to the reviewed
D119 dependency and protocol/doc consistency; it receives final parent review
and CI before merge.

## What actually ships

Extractor, normalizer, and ordinary fact-adjudicator prompts now explain claims,
assertions, facts, entities, source reporting time, and world dates in the same
plain vocabulary. Winning, participating, and enjoying the same event are
different propositions **in both directions**: a win must not collapse into
participation, and participation or enjoyment is not positive evidence of an
existing win. Date correction uses a date-neutral candidate statement and
replaces the chosen window; the prompt does not promise statement rewriting.

The adjudicator no longer serializes the full prepared snapshot to the model. It
projects semantic evidence with attempt-local names (`F1`, `C1`, `A1`, `E1`,
`S1`), factors repeated wording into a `text` dictionary, and translates a
closed `PromptFactDecision` through that attempt's mapping into the existing
`FactApplicationDecision` writer. Empty candidate sets still mint a fact without
a model call. Candidate and evidence membership, locking, CAS, source-existence
checks, and forget inventory are unchanged.

Handle spellings such as F1 are rebuilt from the current mapping. They do not
prove which attempt produced a reply. A stale answer is rejected by the
prepared attempt's compare-and-swap and input fingerprint.

D123 does not send context-only facts, so this lane does not add an `editable`
flag, checker, or mapping registry. Unhydrated window witnesses are named
`W1…` and cannot be cited. Canonical subject/object aliases keep `same_as` /
`canonical_subject` / `canonical_object` relationships from the frozen snapshot.

## Field projection map

Omitted from the model view (administrative): `deployment_id`, `root` as a raw
UUID (the canonical subject is the `subject` E-name), `application_id` as a UUID
(incoming is `incoming_assertion`), `normalizer_version`, `adjudicator_version`,
`membership_hash`, `evidence_hash`, `support_hash`, `ingested_at`,
`invalidated_at`, `extractor_version`, `output_ordinal`, and store UUIDs on
rows.

Kept, renamed, or handled:

| Snapshot | Presentation | Why |
| --- | --- | --- |
| `kind`, `limits`, `potentially_truncated` | same | disclosed caps, not completeness |
| `facts[].fact_id` | `F1…` | attempt-local, not a store id |
| `facts[].statement` | `statement` or `statement_ref`/`text` | complete meaning; repeats factored |
| `facts[].valid_*` | `chosen_*` | already-canonical world window |
| `facts[].window_claim_ids` | `window_claims` plus `window_claims_not_supplied` | hydrated C-names are citable; missing witnesses are W-names, not dropped |
| `facts[].evidence_count`, `contradict_count` | same | current testimony weight |
| `facts[].contradiction_group` | `contradiction_sets` of F-names | live disagreement without the group UUID |
| `facts[].subject/object_entity_id` | E-names, `canonical_*` when different | merged alias ≠ second entity |
| `claims[].claim_id` / `doc_id` | `C1…` / `S1…` | distinct sources stay distinct |
| `claims[].claim_text`, `source_span` | inline or T-ref | original wording, never paraphrased |
| `claims[].asserted_at` | `source_said_at` | reporting time, not world time |
| `claims[].claim_valid_*` | `source_world_*` | raw inclusive source world dates |
| `claims[].is_current_testimony`, `is_attributed` | `current_testimony`, `attributed` | currency and attribution |
| `assertions[]` | `A1…` with `content` | original proposition for support moves; repeated `statement` is factored *inside* content |
| `evidence[]` | F/C names | support/contradict links; incomplete rows increment `evidence_not_supplied` |

Renderer version `concise-handles-1` is in the prepared-input fingerprint.
Adjudicator generations are `relation-adjudicator-2026.09d:concise-handles-3`
and `obs-adjudicator-2026.09d:concise-handles-3` after the lean-instruction
checkpoint. Extractor/normalizer pins still append `assertion-clarity-2`.
These pins are **draft**: D119 PR #400 must be integrated before they are final.

## Tests and commands

Isolated database (this lane only):

`postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55441/ugm_d120_test`

Commands run after the parent semantic/measurement fixes:

```bash
uv run ruff format --check src/ benchmarks/
uv run ruff check src/rememberstack/core/concise_adjudication.py \
  src/rememberstack/model/concise_adjudication.py \
  src/rememberstack/spine/fact_adjudication.py \
  src/tests/core/test_concise_adjudication.py \
  src/tests/spine/test_concise_adjudication_pg.py
uv run lint-imports
python .github/ci/check_test_inventory.py
uv run pyright src/rememberstack/core/concise_adjudication.py \
  src/rememberstack/model/concise_adjudication.py \
  src/rememberstack/spine/fact_adjudication.py \
  src/tests/core/test_concise_adjudication.py \
  src/tests/spine/test_concise_adjudication_pg.py
uv run pytest src/tests/core/test_concise_adjudication.py \
  src/tests/workers/test_claim_valid_time.py \
  src/tests/workers/test_e3_bare_head_noun.py \
  src/tests/benchmarks/test_locomo_protocol.py \
  src/tests/benchmarks/test_locomo_runner.py::test_single_run_summary_json_is_unchanged
REMEMBERSTACK_DATABASE_URL='postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55441/ugm_d120_test' \
  uv run pytest src/tests/spine/test_concise_adjudication_pg.py \
  src/tests/spine/test_fact_application_writer.py \
  src/tests/workers/test_e3_chain.py \
  src/tests/workers/test_lifecycle_reconciliation.py
```

Observed:

- ruff format: 496 files already formatted
- ruff check on adapter files: clean
- lint-imports: 5 contracts kept, 0 broken
- test inventory: unit=106 integration=57 discovered=163
- pyright: 0 errors
- concise unit + claimify + normalizer + protocol + fingerprint: **90 passed**
  at `5273eee8`; lean-prompt re-run of concise+protocol+bare-noun **70 passed**
- PostgreSQL handle + writer after lean prompt: **23 passed**
- E3 chain + lifecycle on the lane DB: **20 passed** (pre-lean; versions for
  those tests follow `FACT_NORMALIZER_VERSION`, unchanged this checkpoint)

These are mocked-provider proofs of translation, application, retries, and
invariants. They are not real-model semantic quality. Prompt-contract tests
check that the instructions exist in plain language; they are not a keyword
classifier proving comprehension.

## Size measurements (reconstructed fixtures, not billed tokens)

tiktoken is not a repository dependency; no tokenizer proxy ran. Counts below
are UTF-8 bytes. They are not billed model tokens and not a conversation price.

Comparison is **previous full prompt + UUID `FactApplicationDecision` schema**
against **new full prompt + handle `PromptFactDecision` schema**, using the
same reconstructed snapshot for each row. The previous prompt is the
`origin/main` adjudicator instruction frozen in the unit test. Prepared
historical snapshots were cleared; every fixture is labeled reconstructed.

| Fixture | What it is | full snapshot | compact input | old prompt+schema | new prompt+schema |
| --- | --- | ---: | ---: | ---: | ---: |
| small mostly-unique | 1 fact, distinct claim/source wording | 2054 | 1332 | 9345 | 10631 |
| varied reconstructed | 6 facts; shared source span; one cross-source claim repeat; wording from local LoCoMo v28 source-linked audit, not a live store | 10166 | 4955 | 17457 | 14254 |
| bounded 20 varied | 20 distinct facts, 40 claims, 40 applications; mixed shared spans, cross-source repeats, unhydrated witnesses; first five statements from the local audit, remainder reconstructed | 57142 | 28760 | 64433 | 38059 |
| best-case repeated | 20 copies of one long sentence (dedup stress, not representative cost) | 66267 | 17080 | 73558 | 26379 |

Schema alone: previous UUID schema 4462 bytes; handle schema 4710 bytes.
Instruction template after the parent's clarity pass: 4597 UTF-8 bytes
(previous shortened template 4393; initial long template approximately 7.1 KB).

The 10:55 parent review was right that the first long instruction offset
compaction on small inputs (then 9345→13142). After combining repeated
definitions/examples and the parent's clarity edits, the small case is
9345→10631 (still larger:
clearer rules plus handle schema). The 6-fact case now saves. The
larger reconstructed case is the bounded 20-fact snapshot
(64433→38059 bytes of prompt+schema). It is not a captured production request.
Best-case exact
repetition remains the largest drop. No billed-token saving is claimed.
Whitespace word counts are not reported as model tokens.

Pre-tighten numbers at `5273eee8` for the same small/6-fact/best-case
fixtures were 13142 / 16765 / 28890. Those are superseded.

## Parent notes addressed

First review:

1. Canonical subject/object: `same_as` and `canonical_subject` /
   `canonical_object` when the frozen snapshot has a redirect.
2. Unhydrated `window_claim_ids`: disclosed as `W1…`; translation rejects them
   as supporting claims.
3. Repeated assertion statements are factored inside `content`.
4. Unnameable evidence/claim/assignment rows increment `*_not_supplied`.
5. Context-only `editable` machinery is not present. Binding design and
   handoff now state D123 adds no context-only producer.
6. Measurements labeled as bytes (optional tiktoken proxy if present).
7. Field map lives in this document, not as a runtime constant.

Second review:

- Bidirectional proposition rule and examples in E2/E3/adjudicator prompts
  and the D120 table. No extra classifier.
- Date-correction example is date-neutral (`won Tournament A` with chosen
  window 5 November). The prompt does not say attach to `won on 5 November`.
- Translator docs/tests no longer claim F1 spelling rejects a stale attempt.
  PostgreSQL `test_stale_attempt_is_rejected_by_cas_not_f1_spelling` publishes
  against a superseded attempt_id and shows CAS miss, while F1 still
  translates on the later mapping.
- Measurements include old prompt+schema vs new prompt+schema on small,
  varied, and best-case reconstructed fixtures.

10:55 whole-request review:

- Adjudicator instruction tightened from duplicated PURPOSE/RULES/EXAMPLES
  prose to one combined text, adapted from
  `/tmp/ugm-d120-d121-grok-20260914/parent-lean-fact-prompt.txt`. Bidirectional
  identity, date-neutral correction, handles, windows, support moves, exclusive
  stored ends, one-microsecond instants, and quarter-aligned calendar bounds
  remain. Exact-word tests no longer freeze section headings or one-way
  example sentences.
- Re-measured with a bounded 20-fact / 40-claim / 40-application reconstructed
  snapshot in addition to small, 6-fact, and best-case fixtures.

## Remaining integration

The parent took ownership of final prompt wording at the user's request.
The latest edit defines world dates at first use, replaces internal shorthand
such as "coexists conservatively" with the actual engine behavior, and corrects
the misleading sentence "a win is not participation": a win can imply
participation, but storing only participation loses the asserted result.
Abandoned design categories and date-dispute machinery are no longer discussed
in the prompt. The extra 204 bytes over the earlier shortened draft are retained
for clarity. The adjudicator generation is `concise-handles-5`.
Parent validation: 64 concise-presentation, protocol, and serialized-run-summary
tests passed. This wording still requires review on the final integrated head;
the earlier Antigravity verdicts do not cover it.

The parent has stacked this branch on D119's published `1debaf80` checkpoint.
The extraction prompts now retain coherent multi-span claims and explicitly
explain origin versus additional supporting passages, source evidence versus
summaries, and preservation of each assertion's meaning. The Claimify task is
stated in ordinary language rather than "decontextualize+decompose+ground".
The extractor generation is `assertion-clarity-3`; LoCoMo is Full-v30 so the
combined processing contract is distinct from D119's Full-v29.

D119's subsequent coordinate/reuse fixes and final main merge still need to be
integrated. The combined draft passed the targeted prompt/temporal checks (117 tests);
after updating its generated protocol fingerprint, the serialized summary
check and PostgreSQL E2/concise-adjudication checks passed together (12 tests).
The final combined head still requires Antigravity review. Earlier reviews apply only to their recorded SHAs. Parent
will also review the additional D122 document-reference instructions when that
runtime is ready. No final merge-readiness is claimed here.

No paid LoCoMo run, no remote store mutation, no merge, no release.

## Review

Antigravity command (read-only inspection of the exact head):

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<review prompt>"
```

Previous reviewed SHA: `5273eee8197d8b7c89c5d12c70c9000661d9d902`.
Verdict then: **APPROVE WITH NITS**. Full text:
[`d120_d121_agy_review_5273eee8.md`](d120_d121_agy_review_5273eee8.md).

Lean-prompt reviewed SHA: `d84af5a48a0f5af1149a4c6bbd5aaf6b201cd5e6`
(prompt commit `ef17559a`). Verdict: **APPROVE WITH NITS**. Full text:
[`d120_d121_agy_review_d84af5a4.md`](d120_d121_agy_review_d84af5a4.md).

Nits were not treated as material: no extra date-qualification example (YAGNI
until D119 lands) and no extra keyword-freeze assertions for `source_world_` /
`chosen_`. D119-integrated final pins still need a later review. This PR is
not merge-ready.


## Review of the parent-integrated wording

Antigravity reviewed exact `ecedaabe4957760ebd690ffd0f2575c078fa0c70`: approve
with nits, no code blockers. The [full report](d120_d121_agy_review_ecedaabe.md)
includes the commands it actually ran. Parent addressed its concrete findings:

- The normalizer now consistently permits zero outputs; its introduction no
  longer contradicts its rules. Normalizer generation is `assertion-clarity-3`.
- The model-facing new-fact-name description includes reserved T/W names,
  matching the existing enforcement and prompt. Adjudicators use
  `concise-handles-5`; the six extra schema bytes are included above.
- Setup docs and variant comments name Full-v30 and the current generations.

The 64 concise-input/protocol/serialized-summary checks pass after these edits.
The changes introduce no new operation or runtime mechanism. Final integration
with D119 and review of that delta remain required before merge.
