# Restore actual visual input to LoCoMo

Status: implementation analysis, 2026-11-10. The owner requires real OCR and
Luna visual descriptions in LoCoMo; dataset captions alone are insufficient.
No new benchmark score or shipped fix is established by this document.

## Observed gap and its history

The retained v38 conv-42 run supplied each image-bearing dialogue turn with
Mistral OCR and a Luna visual description. Its D10:2 reader chunk included
lighting colors. The fresh Full-v40 run supplied a generic dataset caption of
a gaming room and the derived query "gaming setup", without those colors.
Retrieval returned D10:2 successfully, but the required visual information was
absent from the prepared input. The current run scored 15/20; this does not
isolate the effect of its calendar-answer instruction.

Historical commit 9c84eacb9 introduced image preparation and converted-cache
injection into render_session. That commit is retained on the historical
benchmark branch and is not an ancestor of current main. PR447 closed without
merging at88f7bb34; its five commits and nine files did not include the later
batch image converter. PR514 merged selected model/Flex/timeout improvements,
but explicitly retained the caption-only renderer. Configuring a visual model
does not make a text-only preparation path use it. A successful vision role
binding is not evidence that source pixels were processed.

The historical judge also differs. Its prompt SHA72a06363... accepted the core
fact explicitly; current main retains SHAcd4007be..., an older, shorter generic
rubric. The requested model switch was Sol-high to Luna-high. This image repair
keeps the current judge and answer templates fixed so a changed input is not
confounded with a simultaneous scoring-policy change.

Sources inspected: PR447's saved head, commit list and changed files; merged
PR514's description and implementation; historical commit9c84eacb9; current
protocol.py and runner.py; and verified v38/v40 saved reader traces in cloud
PR862. The old/new judge template bytes match their saved run hashes.

## Requirements for the replacement

- Use actual dataset image bytes with the shared ImageOcrDescriptionConverter:
  dedicated Mistral OCR plus Luna vision at low effort, with the approved
  Flex-to-standard routing. Do not introduce a benchmark-specific vision prompt.
- Associate every derived reading with its original sample, session, turn,
  speaker and image ordinal. Handle all image-bearing inputs through one path;
  do not select images using questions, gold answers or evaluation references.
- Freeze source-image hashes, converter/configuration identity, derived text,
  provenance and reported usage in the prepared run. Resuming must not render
  against an ambient mutable cache or silently replace missing images with a
  dataset caption.
- Preserve successful OCR/vision work with the shared independent lane
  checkpoint contract. A failed lane must not re-bill its successful sibling.
  Unknown paid outcomes must remain visible rather than automatically retried.
- Keep explicit execution and finite call/cost ceilings around paid image
  preparation. Include image inputs and preparation metadata in ordinary GCS
  run backups; do not preserve only the final answer text.
- Keep source names and source dates stable. New enriched bytes require a new
  honest protocol/run identity and fresh ingestion. Never relabel the retained
  v38/v40 states, grades or image stamps.

## Integration alternatives to inspect

One option is explicit, immutable preparation using the existing image converter
and its lane checkpoints, then attaching those readings to the correct rendered
turn. This directly restores the successful source context and keeps evaluation
offline once its input bundle is frozen. It must retain converter origins and
record costs rather than treating generated image text as original testimony.

Another option is ingesting images as ordinary companion documents and linking
them to their turns. This naturally exercises worker conversion and metering,
but requires a shipped attachment relationship and enough source context to
avoid losing who attached the image. Do not invent a benchmark-only authority
or bypass E0 to make this option work.

The binding benchmark design must select and describe the supported path before
implementation. Core OCR/vision logic stays shared, not copied from the old
untyped batch script. Old model branches and old generation pins are not restored.

### Integration selected after inspecting the shipped ports

Use an explicit immutable preparation bundle and the existing dual-lane image
converter. The current SDK ingests one file, and the binding file contract does
not expand Markdown's embedded images or invent attachment authority. Adding
a new multi-document attachment contract is unnecessary for this adapter repair.
The converter's full manifest, source map, derived assets and lane receipts stay
in the bundle; the rendered session labels the inserted reading as derived
OCR/visual content attached to its exact turn. The downstream session remains
an ordinary E0 Markdown input. This exercises the shared converter and the
ordinary text ingestion path, not the worker's image-upload route; do not claim
that the session's passthrough origin ranges are native image-origin ranges.

The pinned dataset has910 image URL references and862 distinct URLs. A read-only
inventory of the retained mirror found775 image-turn files and135 explicitly
caption-only entries. Therefore requiring every remote original to remain
reachable would block even the historical successful source set. Missing or
corrupt prepared artifacts and provider failures remain errors. A caller may
explicitly allow recorded remote-image unavailability; each such reference has
a typed reason and visible disclosure, and coverage is reported. No missing
artifact silently selects a caption. The original cached pixels can be imported
as source bytes after hash verification; old unversioned derived Markdown is
not accepted as an attested new conversion.

Cache payment identity is original image hash plus the shared lane fingerprint.
A mutable cache is separate from the immutable bundle copied into the run.
Independent native lane checkpoints preserve successful OCR if vision fails or
changes. A write-ahead conversion marker and usage recorder distinguish a
retryable known failure from an uncertain interrupted paid attempt; the latter
requires inspection rather than automatic regeneration. Concurrent preparation
is bounded and one writer owns a cache. Cost ceilings are reported-cost admission
checks; already admitted calls may finish above the limit. They are not a new
provider hard allowance. Preserve raw sources and all accounting records.

## Verification and paid reuse

Use synthetic fixtures to prove multi-image ownership, actual OCR/visual text
delivery, immutable-input validation and absence of a silent caption fallback.
Count provider calls across successful reuse, partial lane failure and an edited
image: unchanged image bytes and lane identities must not be billed again.
Inspect the benchmark's prepared documents and a real retrieved chunk, not just
environment variables. Run the existing ingestion reuse proofs where the chosen
integration can affect their keys; state any resulting reprocessing boundary.

Source-image retrieval and conversion add preparation cost. Report actual OCR
and vision usage separately and re-estimate the full campaign before activation;
the prior caption-only forecast does not cover this restored work. Missing board
credentials prevent reading Autoboard checklist status; no status is claimed.

Require Antigravity review, normal CI and merge before renewed validation on
latest main. Publication still requires the owner's conv-47 and conv-42 gates
and verified complete GCS archives. Passing a configuration test is not a score.
