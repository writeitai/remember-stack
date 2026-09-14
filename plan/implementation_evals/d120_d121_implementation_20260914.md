# D120/D121 implementation: clear prompts and concise adjudication inputs

**Date:** 2026-09-14
**Lane:** `feat/concise-adjudication-inputs` at worktree
`/Users/jpuc/code/moje/ultimate_memory/ugm-concise-adjudication`
**Design:** [D120](../designs/processing_prompt_clarity_design.md),
[D121](../designs/concise_adjudication_inputs_design.md), commit `4a803c1d` (PR #401, merged).
**This PR / head:** filled after push (see bottom). Parent owns integration, merge, and release.

## What actually ships

Extractor, normalizer, and ordinary fact-adjudicator prompts now explain claims,
assertions, facts, entities, source reporting time, and world dates in the same
plain vocabulary, with contrasting win / participate / enjoy / attribution
examples. The adjudicator no longer serializes the full prepared snapshot to the
model. It projects semantic evidence with attempt-local names (`F1`, `C1`, `A1`,
`E1`, `S1`), factors repeated wording into a `text` dictionary, and translates a
closed `PromptFactDecision` through that attempt's mapping into the existing
`FactApplicationDecision` writer. Empty candidate sets still mint a fact without
a model call. Candidate and evidence membership, locking, CAS, source-existence
checks, and forget inventory are unchanged.

D123 does not send context-only facts, so this lane does not add an `editable`
flag. Unhydrated window witnesses are named `W1…` and cannot be cited.
Canonical subject/object aliases keep `same_as` / `canonical_subject` /
`canonical_object` relationships from the frozen snapshot.

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

Renderer version `concise-handles-1` is in the adjudicator generation and the
prepared-input fingerprint.

## Tests and commands

Isolated database (this lane only):

`postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55441/ugm_d120_test`

Commands run:

```bash
uv run pytest src/tests/core/test_concise_adjudication.py \
  src/tests/workers/test_claim_valid_time.py \
  src/tests/workers/test_e3_bare_head_noun.py \
  src/tests/benchmarks/test_locomo_protocol.py
# 95 passed with the PG file included on the lane DB

REMEMBERSTACK_DATABASE_URL='postgresql+psycopg://rememberstack:rememberstack_test@127.0.0.1:55441/ugm_d120_test' \
  uv run pytest src/tests/spine/test_concise_adjudication_pg.py \
  src/tests/spine/test_fact_application_writer.py \
  src/tests/core/test_concise_adjudication.py \
  src/tests/benchmarks/test_locomo_runner.py
```

Observed: concise unit + claimify + normalizer + protocol **pass**; PostgreSQL
handle attach / retry / date clearing / support-move / source-delete /
unhydrated W-name **6 passed**; existing writer suite **pass**; LoCoMo runner
unit **pass** after pinning protocol identity Full-v29.

Pyright on the adapter files: 0 errors. Ruff on the touched files: clean.

These are mocked-provider proofs of translation, application, retries, and
invariants. They are not real-model semantic quality.

## Size measurements (reconstructed fixtures, not billed tokens)

tiktoken is not a repository dependency; no tokenizer proxy ran. Counts below
are UTF-8 bytes. Whitespace word counts on minified JSON are not model tokens
and are not reported as such.

Small fixture (one win, one unhydrated window witness):

| Object | UTF-8 bytes |
| --- | ---: |
| full snapshot | 2047 |
| compact input | 1292 |
| rendered prompt (template + compact input) | 7724 |

Large reconstructed fixture (20 facts/claims/assertions sharing one long
statement, plus one unhydrated witness each):

| Object | UTF-8 bytes |
| --- | ---: |
| full snapshot | 66479 |
| compact input | 17080 |
| rendered prompt | 23512 |
| `FactApplicationDecision` JSON schema | 4462 |
| `PromptFactDecision` JSON schema | 4549 |

No fixed saving or conversation price is claimed. Schema descriptions make the
handle schema slightly larger than the UUID schema; the input drop is from
omitting bookkeeping and factoring repeated text.

## Parent preliminary notes addressed

1. Canonical subject/object: `same_as` on entities and `canonical_subject` /
   `canonical_object` on rows when the frozen snapshot has a redirect. Merged
   alias fixtures compare meaning (same E canonical), not field-name presence.
2. Unhydrated `window_claim_ids`: disclosed as `W1…`; translation rejects them
   as supporting claims. Hydrated membership is not expanded.
3. Repeated assertion statements are factored inside `content` (`statement_ref`);
   the full duplicate is not kept beside the ref.
4. Evidence/claim/assignment rows that cannot be named increment
   `evidence_not_supplied` / `claim_not_supplied` / `assigned_to_not_supplied`
   instead of vanishing.
5. Context-only `editable` machinery removed: D123 does not produce that
   payload. Real handle validation remains (unknown, wrong kind, W-names, stale
   attempt CAS, source deletion).
6. Measurements labeled as bytes (and optional tiktoken proxy if present). No
   whitespace word count is called a model token.
7. Field map lives in this document, not as a runtime constant.

## Remaining integration

D119 PR #400 is still implementing multi-span extraction. This lane does not
change E2 `source_span` schema or D119 generation pins. After #400 merges,
rebase and project any additional exact-span fields the claims snapshot gains;
do not treat extra span lists as bookkeeping. Protocol Full-v29 may need a
further identity roll if D119 also changes extractor semantics.

No paid LoCoMo run, no remote store mutation, no merge, no release.

## Review

Antigravity command (read-only inspection of the exact head):

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<review prompt>"
```

Exact reviewed SHA and verdict: filled after that review.
