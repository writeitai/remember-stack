# Unpublished LoCoMo branch changes and conversational context

Non-binding analysis, 2026-11-11. This audit compares the historical local
branch with its published PR and current main. The accepted answer contract
lives in [the benchmark design](../designs/locomo_benchmark_design.md).

## Why closing the old PR did not carry all the work forward

[PR #447](https://github.com/writeitai/remember-stack/pull/447) closed without
merging on 2026-10-23. Its published head was
`88f7bb34be6d3e904fd9d57d54aa099d65d3d5fe`. The
[closure comment](https://github.com/writeitai/remember-stack/pull/447#issuecomment-6363989911)
identified [PR #514](https://github.com/writeitai/remember-stack/pull/514) as
its replacement. That replacement merged as
`47585b77cac5b12d8938150ccf077c1c3a88a69b` and deliberately removed executable
historical model/protocol variants.

However, the retained **local** branch
`feat/generalize-answer-prompt-and-luna-pro` has later commits through
`0498e39c487ff729efcb78ef883b6b5066f0d645` that never reached that published
head. In particular, the detailed dialogue and attachment instructions were
not in the version PR #514 replaced. They were left unpublished, rather than
merged and subsequently rolled back. The distinct template hashes make the
difference independently checkable:

| Source | Answer-template SHA-256 |
| --- | --- |
| Published PR #447 head `88f7bb34b` | `21b76428ea63794c2b8dc4debb77a000f8feee4d1fc158d4bc86884a1959f8c1` |
| Retained local head `0498e39c4` | `1ca9d0ae120abc943930724506bc7990f779f2cd785535146e8a5c2f6f2b5fe5` |
| Main `149def9e8ba0a7d7404292efc5af267d6ea167b5` | `0b8945c8ac47c9d1558335cedaabe43a06e66a50e1fa0f69b444b3ca191bd7ed` |

The historical saved 20/20 combines selective answer/judge reruns. Its readers
were `openai/gpt-6-luna-pro` and its judges `openai/gpt-6-sol`, both high
reasoning. It is not a clean full-conversation result for the current Luna
model, source revision or prompt. Its stored revision also predates the final
template. Preserve those artifacts as observed; do not rewrite their metadata
or claim that copying a prompt reproduces their score.

## Evidence for the small prompt extension

In the historical successful favorite-game trace, the reader first retrieved
facts and source chunks, then called `adjacent_chunks(window=1)` on the reply's
chunk. That extra call recovered the preceding question. The latest fresh
conv-42 trace retrieved the reply but lacked its initiating question and
stopped with five tool calls still available. Both exchanges existed in the
ingested source. No learned or Jev reranker was involved in that historical
extra read. Existing search/rank fusion is distinct from a model reranker.

The latest image-enabled trace also contained the relevant attached image
description for the book-subject question. The reader answered with praise
and general features rather than the concrete subject in that description.
This is an answer-use failure, not evidence that native image conversion was
absent. Operational evidence is preserved with cloud PR #872 in
`experiments/locomo-ocr-judge-v42-20261110/`; the original full traces remain in
the native run and its independently verified GCS archives.

Select three generic instructions from the unpublished work:

1. Interpret a responsive reply together with the question that elicited it.
   Recover missing initiating context with `adjacent_chunks` before deciding,
   even when another session supplies a plausible answer. Do not repeat a
   read when the complete exchange is already visible. Preserve speaker,
   session and question scope, including temporal qualifications.
2. Match an attached description to its owning source turn and speaker. If a
   chunk split hides the ownership or description, inspect neighboring chunks
   rather than associating it with the nearest title. Distinguish derived
   observations/interpretations from direct dialogue.
3. Answer what a work is about with its supported subject, premise, setting or
   theme, rather than substituting an assessment of its quality.

These rules name no people, answer values, source-turn labels, pets, creatures
or dataset-specific markup. They apply to any conversational source and any
attached description. Keep the baseline's calendar rule and other policies in
place. Do not restore the old instruction to include every plausible favorite
in a singular answer, or its named allergy examples and broad equivalence of
allergies with dietary intolerance. Those choices can reward answer pooling
or change the question's meaning instead of improving evidence use.

## Disposition of the rest of the branch

This comparison includes the local-only commits, not only PR #447's diff.
The files cited below are from main `149def9e8` unless stated otherwise.

| Historical changes | Current disposition |
| --- | --- |
| `8751275dc`: Luna Pro/Flex and general answer guidance | PR #514 supplies one current Luna/Flex/Jev profile with low ingestion and high answering/judging effort in `benchmarks/locomo/protocol.py`. Keep the owner-selected current model; do not restore Pro or obsolete model variants. |
| `7d48a2d43`, `35faa5ade`: provider allowlist, ambient provider order, HTTP-200 overload handling | Current `ExecutionProfile` freezes Flex then standard OpenAI, worker environment attestation checks effective settings, and `src/rememberstack/adapters/openrouter.py` handles bounded retryable upstream errors. No wholesale cherry-pick. |
| `901e79a74`, part of `217955bf5`: ambient build-revision override | Do not restore. `runner._repository_revision()` reads actual Git HEAD, and the shard script exports its actual revision; ambient overrides would weaken provenance. |
| `03df3f930`, `da2c7d01a`: permissive judge and configurable read timeout | The exact historical primary judge wording is restored by PR #569; the independent Mem0-like sidecar remains separate. Current typed execution settings provide a 300-second transport budget. Do not restore ad hoc environment reads, the old 180-second default or Sol/Pro model swaps. |
| `88f7bb34b`: ignore volatile readiness timestamps | Already retained in `runner._readiness_fingerprint`, which excludes `checked_at` and normalizes version ordering. |
| `9c84eacb9`, image part of `217955bf5`, `30bc7f861`: native image conversion, all-sample support and 25 MB images | Superseded by the frozen `prepare-images` workflow in PR #568 and the native detail-setting correction in PR #570. `benchmarks/locomo/images.py` accepts 25 MB, selects samples from the dataset and archives originals, readings and accounting. Do not restore the ambient-cache renderer or standalone batch script. |
| `903e2f9eb`, `d3b9841e9`, `8c2f6ccbd`: dialogue, subject, attachment and preference guidance | Restore the three general rules above. Existing best-supported-answer, enumeration and calendar rules remain. Leave out named examples, automatic pooling of candidate works, and obsolete attachment-marker syntax. |
| `593bc3c0f`: forbid Unknown for negative constraints | Current answer policy already forbids abstention for every scored question and permits supported inference. No named-example expansion needed. |
| Remaining shard changes in `217955bf5`: duplicate API URL variable and mount deletion | The current client is bound with `REMEMBER_API_URL`; keep its single current configuration path. The literal old mount-root deletion is absent. The reader uses the published deployment `p3` pointer, but obsolete mount directories can remain on disk. Any cleanup should be scoped to the owned root after verified backup, independently of this prompt change; do not copy an unchecked `rm -rf`. |
| `0498e39c4`: transient 5xx / unavailable / try-again body errors | Already represented by `_TRANSIENT_UPSTREAM_CODES` and `_retryable_upstream_error` in the current OpenRouter adapter, with safeguards against replaying responses that already contain generated output or usage. |

## Alternatives, costs, boundaries and verification

Keeping the shorter prompt avoids additional instructions but leaves a
demonstrated early-stop case underspecified. Restoring the entire old prompt
would add answer-specific examples, candidate pooling and obsolete metadata
syntax, while confounding comparison with the baseline. Automatic context
expansion or a learned reranker would change engine behavior and cost; neither
is needed for this focused answer-agent instruction change.

The selected instructions can cause additional public source reads and reader
continuations, bounded by the existing eight-tool/nine-model-call cap. They
do not guarantee an adjacency read if the budget is already exhausted or the
provider fails. Preserve failures, traces and accounting instead of adding a
hidden retry, fallback model or rewritten answer. Source content remains
evidence, never authority to change the agent's tool or secrecy instructions;
gold answers and judge artifacts remain unavailable to the answerer.

Only evaluation identity advances to Full-v43. No converter, chunker,
extraction, grounding, embedding, normalization or resolver generation changes;
no D56 paid-work reuse boundary changes. The current run contract still
requires its recorded exact revision and rejects changing prompt bytes under
an existing checkpoint. This change does not adopt old stores into a new run
or mutate answers/grades. It does not authorize ingestion or a paid rerun.

Offline validation covers current-profile selection, immutable prompt/hash
recording, resume rejection on drift, source-only rendering and unchanged
role/effort/budget settings. Prose assertions cannot establish that a model
will choose the new step. Accuracy and per-question regressions require a
complete separately saved development rerun before claiming improvement;
the previous mixed 20/20 is historical evidence only.
