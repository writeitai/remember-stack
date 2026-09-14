# D120/D121 implementation: clear prompts and concise adjudication inputs

**Date:** 2026-09-14
**Lane:** `feat/concise-adjudication-inputs` at
`/Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication`
**Design:** [D120](../designs/processing_prompt_clarity_design.md),
[D121](../designs/concise_adjudication_inputs_design.md), commit `4a803c1d`
(PR #401, merged).
**PR:** https://github.com/writeitai/remember-stack/pull/402 (draft)
**Head:** `42b26390afefb4dc82b9d63ad3faf9d6bda4a549` (this report's commit; Antigravity SHA filled after review).
**Parent owns** integration, merge, and release. User authorized the checked
CLA text.

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
Adjudicator generations are `relation-adjudicator-2026.09d:concise-handles-2`
and `obs-adjudicator-2026.09d:concise-handles-2`. Extractor/normalizer pins
append `assertion-clarity-2`. These pins are **draft**: D119 PR #400 must be
integrated before they are final.

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
- PostgreSQL handle attach / retry / date clearing / support-move / source-delete
  / unhydrated W-name / **CAS stale-attempt (not F1 spelling)**: **7 passed**
- existing writer suite: **16 passed** (23 PG tests together)
- E3 chain + lifecycle on the lane DB: **20 passed**

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
| small mostly-unique | 1 fact, distinct claim/source wording | 2054 | 1332 | 9345 | 13142 |
| varied reconstructed | 6 facts; shared source span; one cross-source claim repeat; wording from local LoCoMo v28 source-linked audit, not a live store | 10166 | 4955 | 17457 | 16765 |
| best-case repeated | 20 copies of one long sentence (dedup stress, not representative cost) | 66267 | 17080 | 73558 | 28890 |

Schema alone: previous UUID schema 4462 bytes; handle schema 4704 bytes.

On a small unique snapshot the clearer instruction is larger than the data
saving. Varied text is roughly even. Best-case exact repetition is where
factoring dominates. No fixed saving is claimed. Whitespace word counts are
not reported as model tokens.

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

## Remaining integration

D119 PR #400 is still implementing coherent multi-span extraction. This lane
does not change E2 `source_span` schema. The E2 Claimify prompt still contains
main's "simplest standalone claims" wording; D119's coherent-claim language
must be preserved at rebase, not overwritten. After #400 is ready, rebase,
project any additional exact-span fields the claims snapshot gains, and
regenerate extractor/normalizer/adjudicator generations and LoCoMo Full-v29
pins from that combined source. Do not treat the current E2 prompt, schema,
or protocol pins as final.

No paid LoCoMo run, no remote store mutation, no merge, no release.

## Review

Antigravity command (read-only inspection of the exact head):

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<review prompt>"
```

Exact reviewed SHA and verdict: filled after that review.
