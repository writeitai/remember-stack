# Preserve visible identity labels in image observations

Non-binding analysis, 2026-11-13. The audited frozen map photograph had a legible
park title and district name that OCR omitted and the vision description did
not repeat. `_DESCRIPTION_PROMPT` explicitly prohibits text transcription. A
retrieval change cannot recover a name absent from the canonical reading.

Current D65/D115 conversion already pays for independent OCR and one vision
call on byte-identical original pixels. Keep those lanes, provenance and
D54's one-source counting. Permit a narrowly bounded observation such as
"The sign reads as 'NORTH GATE'" for clearly legible identity labels. Do not
ask vision to duplicate paragraphs or tables, correct OCR or infer obscured
characters. Uncertain identification remains an interpretation.

This is general to product labels, map titles and diagram labels. No benchmark
IDs/answers/entities enter runtime prompts or tests. No new provider, crop,
field, classifier or model call is necessary. OCR output stays unchanged under
its own heading; quoted visual labels remain `model_observation`, not OCR.
If the readings disagree, retain both and let a reader inspect the original.
No semantic recall or accuracy guarantee follows from these instructions.

A full vision transcript would double large text readings and increase token
cost up to the output limit. Bound the prompt to at most eight identity labels,
at most80characters each, and no running prose/tables. Treat these as prompt
limits within the existing output character/token caps, not a new guaranteed
measurement or reason to truncate other useful visual descriptions. Keep scene,
color, shape and relationships alongside identifying labels.

Roll `DESCRIPTION_PROMPT_VERSION`; the description checkpoint and composed
converter identity change, while OCR's independent fingerprint does not.
At conversion only, zero additional OCR calls and one new description call are
required when that source's OCR checkpoint exists. New representation text can
change its chunks' extraction/embedding products, same-section neighbors and
D122 Claimify for up to eight following chunks. All such paid work must follow
normal reuse rules; do not claim description-only cost for the whole pipeline.
An ordinary new version with changed converter provenance may also change text
origin identity under the current D140 coupling. There is no promise that an
unchanged visual region retains its prior timestamp across toolchain changes.

Leave existing representations and image bundles frozen. The native bundle
loader rejects a different requested converter fingerprint, so a new profile
requires new visual readings; it cannot silently reuse/relabel old ones.
Keep this separate from reader PR593, whose unchanged processing/image profile
can be evaluated on retained stores. No evaluation, reingestion, deploy or GCS
mutation is part of this PR. A new protocol family identifies the changed
image-generation contract; old results remain archived, not new-profile data.

Validate independent checkpoints, source pixels, observed/interpretation/OCR
ranges, output bounds and unchanged image models/settings; run existing D56
real two-version PostgreSQL proofs in an isolated database. Mocks prove
assembly/reuse, not reading quality. The number of description calls is unchanged
per ordinary fresh conversion; extra quoted text remains within current caps.
Its token cost is returned token delta times the configured output rate; record
real costs before any paid campaign. No prices or increased spend are assumed.
