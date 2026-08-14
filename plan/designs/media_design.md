# Media — Pictures, Video, Audio (Design)

How the memory system ingests, grounds, searches, and serves **media**: standalone images,
audio recordings, video. Binding design for decision **D65**, realizing the requirement that
drove it: *the memory ingests the **derived** information (transcripts, descriptions); the
consuming agent keeps access to the **raw** files whenever it decides it needs them.*
Research: `plan/analysis/media_handling/` (internal + Codex parallel analyses + SYNTHESIS).
This document is the one self-contained home for media; the touched designs (e0, e1,
lifecycle, e2_e3, retrieval) carry surgical cross-edits pointing here. Numbers and tool picks
are starting points to measure (CLAUDE.md).

> **Reading this cold (CLAUDE.md Rule 1) — the vocabulary, because none of it is assumed:**
>
> - **ASR** (automatic speech recognition) — a model that turns audio into a text
>   *transcript* (Whisper-class). Modern ASR also emits **timestamps** per segment or word.
> - **Diarization** — determining *who spoke when* in a recording ("Speaker 1: …, Speaker 2:
>   …"), and, where identity is resolvable, *which person* each speaker is. Without it a
>   transcript is a wall of unattributed speech.
> - **VLM** (vision-language model) — a model that can *look at an image* and produce text
>   about it: describe it, read text visible in it, answer questions about it. (Claude and
>   GPT-4 with vision are VLMs.) In this pipeline the VLM is the tool that turns a photo into
>   its text description, the way ASR turns audio into a transcript.
> - **OCR** vs **description** — OCR extracts *text that is literally visible* in an image (a
>   scanned page, a slide); a description is the VLM's *account of what the image shows* ("a
>   workshop bench with a disassembled pump"). The first is a rendering of symbols; the
>   second is a model's observation. The distinction matters all the way down (§5).
> - **Embedding** — a vector representation of content such that similar meanings land near
>   each other; the system's semantic search already works this way over *text* (D8/D63).
>   **Cross-modal embedding models** (CLIP-class) map *images* (or audio) and *text queries*
>   into the *same* vector space — so the query text "small red connector" can be compared
>   directly against the pixels of stored images, no description involved.
> - Existing machinery this design builds on: every input becomes `document.md` (clean
>   Markdown — the immutable coordinate system all offsets point into, D57) via a versioned
>   **converter** (D38); the **blockizer** derives blocks; PageIndex draws sections; E2
>   extracts **claims** whose `source_span` offsets point into document.md (grounding, D32);
>   the **raw mount** (D51) serves immutable originals read-only, *off* the navigation path,
>   via explicit pointers; `media/` in the artifacts bucket holds *derived* media on the
>   browse path; facts count **distinct source lineages** (D54).

## 1. The conceptual model (confirmed, now bound against media)

**A media file is a source whose testimony reaches the system through a lossy, versioned
transcription, with the original always one explicit pointer away.** Three objects, three
jobs — never conflated:

1. **The source asset** — the immutable raw bytes (`raw/<doc_id>/<content_hash>/original.ext`).
   The audit target, and what a multimodal agent inspects when it decides the derivation
   isn't enough (listen to the tone; look at the picture). Never copied into browse trees,
   never replaced by its derivation.
2. **The representation** — the Markdown-first, model-derived *reading* of the asset:
   `document.md` + its sidecars (source map, manifest, blocks), produced by a pinned converter
   route (§2). A representation is an **identified immutable object** (`representation_id`,
   §6): one document version can own *several* representation generations over its life (the
   2026 ASR's reading and the 2027 ASR's reading of the same bytes are two objects, both kept),
   and exactly one is the version's **current** representation. This is what E1 blockizes, E2
   extracts from, P1 text-searches, and P3 summarizes. **All text eligible for
   extraction/search lives in `document.md`** — text existing only in a sidecar (e.g. a
   `.vtt` subtitle file) is invisible to the blockizer, E2, search, and grounding, and
   therefore does not exist as testimony.
3. **Derived media assets** — regenerable children in `media/`: extracted figures, video
   keyframes, crops, thumbnails, optional interchange transcripts (`.vtt`/JSON —
   *interchange*, never canonical). Each carries its own hash and a locator (§4) back to the
   source region/time it came from.

**Media is an E0 input modality, not a new plane or a parallel pipeline.** Once the converter
has produced the representation, nothing downstream is media-specific except the provenance it
carries (§4) and the disclosure it inherits (§5). PageIndex is *not* extended to media:
structure is drawn over the derived text; media assets hang off it as linked artifacts.

## 2. Converter routes (extends the D38 router table)

The router gains three media routes, each a versioned converter like every other
(`converter_name`/`converter_version`; a model upgrade is a version bump — §6):

| Input | Route | document.md carries | media/ carries |
|---|---|---|---|
| **Audio** (`audio/*`) | **diarized ASR** | the transcript, one block per speaker turn, speakers resolved to entities where possible ("**Bob:** …"), unresolved speakers kept as stable labels ("**Speaker 2:** …"); an optional **Acoustic events** section (non-speech sounds the tool detects — alarms, applause — capability-dependent) | optional `.vtt` interchange copy |
| **Video** (`video/*`) | ASR (audio track) + **adaptive keyframes** + optional VLM shot notes | the transcript as the document spine, keyframe references at their time positions (exactly like figures in a paper), shot notes as clearly-sectioned blocks; optional Acoustic events as for audio | keyframes (adaptive: per shot, not per frame — coverage is a measured knob), thumbnails |
| **Standalone image** (`image/*` that is a *picture*) | **VLM description** (+ OCR of any visible text) | the description + a "Visible text" section, clearly sectioned; region-grain descriptions permitted as sub-sections with image-region locators | a normalized preview/thumbnail |
| *(image that is a document* — a scanned page, a slide deck export*)* | the existing OCR route | as today | as today |

Notes an implementer needs:

- **The image discriminator.** MIME alone cannot distinguish "image that is a document" (→
  OCR route) from "image that is a picture" (→ description route); the route includes a
  cheap classifier (or the VLM's own routing call). Misroutes are recoverable — both routes
  are versioned conversions of immutable bytes; a route fix is a version bump.
- **Diarization is load-bearing, and conservative.** Attributed stance (D59) requires a
  holder: without speakers, every opinion in a meeting recording is holderless and Selection
  drops it. But *wrong* attribution corrupts stance memory, while *missing* attribution
  merely loses claims — so the route resolves a speaker to a person only on positive evidence
  (self-introduction, calendar/participant metadata in the bundle, registry match) and
  otherwise keeps the stable anonymous label. Any changed diarization generation is a
  converter version bump flowing §6 — never an in-place edit of an existing representation.
- **The sectioned Markdown shape — and the mode-homogeneity rule.** Each route emits
  `document.md` with **structurally separated sections by derivation kind** —
  `## Transcript`, `## Acoustic events`, `## Visual description`, `## Visible text (OCR)`,
  `## Shot notes` — and the manifest labels **contiguous character ranges** of the output
  with their `derivation_kind` + `evidence_mode` (§5). The binding rule: **every labeled
  range is mode-homogeneous** — a converter must emit the model's *interpretations* ("Alice
  looks hesitant") in ranges labeled separately from its *observations* ("Alice enters the
  room"), never interleaved inside one label. This is a converter output-contract obligation
  (the route prompt/adapter enforces it), and it is what makes §5's disclosure a property of
  *ranges the converter wrote*, not a per-claim judgment anyone has to make downstream.
- **The generalized converter contract** (refines D38 a second time):
  `convert(bytes, mime, hints) → { document.md, source_map, derived_assets[], manifest }` —
  the *page map* generalizes to a **source map** (§4), `derived_assets` are the `media/`
  children with their locators, and the `manifest` is the route's **complete self-account**,
  with required fields (nullable only where a capability is genuinely absent):
  the route taken and the full component graph (models + versions per stage);
  the **execution context** (which adapter ran each model, local vs provider — the D61 port
  record privacy audits need); **output hashes** (document.md, source map, each derived
  asset — the representation's identity inputs, §6); the range→`derivation_kind`/
  `evidence_mode` labeling (§5); **selected tracks** (which audio track, for multi-track
  video); the **coverage policy and result** (keyframe sampling policy chosen, intervals
  actually covered — adaptive sampling must report what it skipped, the no-silent-caps rule
  applied to conversion); and **gaps + warnings** (corrupt intervals, unsupported codecs,
  regions the tool could not read) — a conversion that silently drops ten minutes of a
  recording is the same lie as a silent top-k.

## 3. What "already works" and stays untouched

The representation flows the standard pipeline with **no media-specific machinery**: blocks
(one per speaker turn / description paragraph), sections (PageIndex topical segments over the
transcript; the synthetic root covers short clips; the existing `role` enum suffices), chunks,
claims, facts, K pages, P3 stubs. Counting needs nothing new: a caption and a transcript of
the same video are two views of **one** source lineage — D54's distinct-lineage rule already
keeps them one witness, not two.

## 4. Source locators — grounding to a *moment*, on every surface

**The problem:** block provenance was `{page?, bbox?}` — built for paper. A claim extracted
from minute 14 of a recording could only point at *the whole file*; the agent following the
raw pointer got 90 minutes to scrub. **The fix — the typed `SourceLocator` union. This is
the normative schema; every other document (e1 §2, the schema, the eval checks) points here:**

```
SourceLocator =                          -- a discriminated union on `kind`
  | { kind: page,         page, bbox?,                       precision: page | region }
  | { kind: source_range, start_offset, end_offset,          precision: exact | approximate }
  | { kind: image_region, region,                            precision: image | region }
  | { kind: time,         start_ms, end_ms, track?,          precision: word | segment | shot }
  | { kind: video_region, start_ms, end_ms, region?,
      keyframe_asset_id?,                                    precision: segment | shot | frame }
```

Field conventions (fixed here so two implementers cannot diverge):

- `page` is **1-based**. `bbox`/`region` is a normalized rectangle `{x, y, w, h}`, each in
  `[0, 1]`, **origin top-left** of the page/image/frame, axis-aligned.
- `source_range` is the **pageless** case (HTML, email, plain text — sources with character/
  byte structure but no pages): offsets into the *source* representation the converter read,
  as the converter's manifest defines them. It exists so pageless sources are mapped rather
  than left with a null locator.
- Time intervals are **half-open** `[start_ms, end_ms)`, integer milliseconds on the raw
  asset's **primary media timeline as decoded** (the manifest names the timeline and the
  selected track) — never formatted strings, never frame numbers (variable-frame-rate video
  makes frames non-portable). `00:14:33` is a *rendering* of `start_ms=873000`. `track`
  names an audio track by the manifest's track table; `keyframe_asset_id` names a derived
  asset in `media/`.
- **The pin lives on the carrier, not in the union.** A locator never travels alone: every
  record that carries one (a source-map entry, a block, an evidence occurrence, an envelope
  provenance item) names the **document version** (`version_id`/`content_hash`) and the
  **representation** (`representation_id`, §6) it belongs to, and the raw asset resolves
  from the version (`content_objects.raw_uri`). Never a lineage, never a P3 path: a claim
  extracted from the 2024 version of a living file must deep-link into *those* bytes, not
  this week's replacement.
- **One span may map to several locators.** A source-map entry maps a character interval to
  a locator **list** (a sentence assembled across a page break or an edit cut is two
  locators); consumers render all of them. When a claim's `source_span` intersects several
  source-map entries, its locator set is the union of the intersected entries' locators.

Rules, each load-bearing:

- **Precision-honest.** ASR provides at least segment timestamps (word-level where the tool
  supports it); the locator says which (`precision: word | segment`). The system never
  fabricates word timing by interpolating characters across a segment. Every variant carries
  `precision` — a consumer can always tell how tight the pointer is.
- **The source map** connects `document.md` character intervals to locators — the page map
  generalized. The **grounding chain becomes two hops**: claim → `source_span` (exact,
  deterministic — D32 unchanged) → source-map intersection → raw locator (converter
  precision, disclosed). The first hop proves the claim derives from the representation; it
  **cannot** prove the ASR heard correctly — which is why D32's sampled independent audit
  becomes **modality-aware**: the auditor of an ASR claim *listens to the referenced
  interval*; of a VLM claim, *looks at the referenced frame/region*. Auditing only the
  derived Markdown would grade the converter against its own output.
- **Deep links on every surface.** P3 stubs and `document.md` frontmatter render locators as
  raw-mount-relative links with media fragments (`original.mp3#t=873`); the retrieval
  envelope's provenance handles carry the locators; and unmounted parity requires a
  **locator-aware serving operation** (`hydrate depth=bytes` with a time-range/region, or a
  `source_open` primitive) returning a seekable, codec-aware segment — a naive byte-range is
  a false promise for arbitrary video codecs. Clip extraction is a *serving* operation, never
  a new stored artifact.
- **Three kinds of time, named apart** (schemas, API fields, the consumption skill):
  `start_ms` = where in the *file* the evidence occurs; `claim_valid_from` (D41) = when the
  fact held *in the world*; `ingested_at` = when the *system* learned it. Calling any two of
  these "the timestamp" invites wrong as-of queries.

## 5. Derivation disclosure — the reader always knows how mediated the text is

Claims extracted from media-derived text are **model-mediated testimony**: the ASR may
mishear; the VLM may hallucinate a detail. The mediation is already *auditable* (converter
versions + the raw audit path) and *correctable* (§6); this section makes it **visible at
read time**, because three kinds of media-derived text have genuinely different relationships
to the source:

| `evidence_mode` | Meaning | Example |
|---|---|---|
| `source_expression` | a fallible rendering of symbols/speech *present in the source* | a transcript sentence; OCR'd slide text; an embedded caption |
| `model_observation` | the model's account of what the source *shows* | "the image shows a red valve"; "Alice enters the room" |
| `model_interpretation` | the model's *reading into* the source | "the speaker sounds hesitant"; "the chart implies strong growth" |

Implementation is deliberately cheap and deterministic — **no per-claim judgment exists
anywhere**: the converter's manifest (§2) labels contiguous, **mode-homogeneous** character
ranges of `document.md` with `derivation_kind` (asr | acoustic_events | vlm_description |
ocr | shot_notes | passthrough …) and `evidence_mode` — every route emits labels (passthrough
text routes label everything `passthrough`/`source_expression`), so the labeling is **total**,
not a media special case. **Claims inherit the labels through their `source_span` →
labeled-range intersection** — with one deterministic tie-break: a claim whose span crosses
ranges with *different* modes takes the **most-mediated** mode of any range it touches
(`model_interpretation` > `model_observation` > `source_expression`) — disclosure errs toward
disclosing more mediation, never less, and no splitting machinery is needed. The resolved
labels are **cached on the claim's occurrence record** (`chunk_claims`, together with the
resolved locator set — the occurrence-grain provenance home, schema §7), because they are
occurrence facts: the same transcript sentence re-derived by a new ASR generation is the same
claim text with a different derivation record. The retrieval envelope surfaces both **per
evidence item** (§7 of `retrieval_design.md` — never as one flattened label on a fact), so an
agent reading "Alice looked hesitant" sees `model_interpretation (vlm)` on that evidence and
weighs it accordingly.

Three boundaries, each explicit:

- The mode is **disclosure, never a verdict**: Selection's verifiability rules still decide
  what is kept — a model's interpretation faces the same bar as any evaluative text — and no
  code path auto-drops, down-ranks, or invalidates on `evidence_mode`.
- `evidence_count` still counts **source lineages**, never derivation runs.
- **The correlation policy is bound now, not deferred:** distinct-lineage counts are the
  system's *only* confidence input, and derivation-family provenance (which converter family
  produced the evidence) is **disclosure-only** — surfaced in the envelope so a *caller* can
  see that ten supporting images were all captioned by one VLM family (one systematic
  perception error, not ten independent witnesses — D42's independence caveat), but never
  fed into any count or rank by the system itself. A correlation-aware confidence adjustment
  (discounting same-family corroboration) is a **documented alternative, deliberately not in
  the system**: it would put a modeling judgment inside a mechanical count, and the callers
  are agents who can read the disclosure and judge.

## 6. Lifecycle: a better model is a version bump (and the identity model now supports it)

**The representation is an identified immutable object.** A conversion run's output —
`document.md`, the source map, `blocks.json`, the manifest, the derived assets — is one
**representation generation**: `representation_id`, belonging to exactly one document
version, stamped with the route + full component versions and the manifest's output hashes,
**never mutated after creation**. Artifact paths carry the representation dimension —
`gs://…-artifacts/<doc_id>/<content_hash>/<representation_id>/document.md` — so a
re-conversion of unchanged bytes **cannot overwrite** the coordinate system that historical
claims' spans and locators resolve against (schema: a `document_representations` table; the
version's `current_representation_id` points at the live one). One byte object, several
readings: `content_objects`' "converted once" is per *(bytes, route, component versions)* —
identical bytes are never re-converted under the same toolchain, and a new toolchain is a new
representation object beside the old one, never in place of it.

**An ASR/VLM upgrade** creates a new representation → new blocks → reuse keys miss →
re-extraction. That is the **processing-driven** ruleset of D54, exactly as for an extractor
upgrade: new claims replace old ones in currency, counts don't move (same lineage), a fact
the new conversion doesn't re-derive is flagged `support_withdrawn` — **never** retracted
(nothing about the *source* changed). **The current pointer swaps only on completion**: the
new representation becomes current after its conversion → E1 → E2 chain has finished (the
same completion rule reconciliation already binds — no window where old testimony is retired
and new testimony hasn't landed).

**The extraction basis, precisely.** Three identities, kept apart
(`evidence_lifecycle_design.md` §1/§3 updated):

- the **source snapshot** — `version_id` (which bytes; changes when the *source* changes);
- the **representation** — `representation_id` (which reading of those bytes; changes when
  the converter toolchain changes);
- the **extraction basis** — `(representation_id, blockizer_version, structurer_version,
  extractor_version)`: everything whose change means "same testimony, re-derived"
  (structurer included: section roles feed Selection, so a structurer bump is a
  re-extraction boundary — already true in D56's `extraction_input_hash`, now named in the
  basis).

Old representations remain resolvable forever (historical grounding); re-runs replay stored
output per D7 — a nondeterministic model is never silently re-called for the same
representation.

## 7. Media search — because a description can't mention everything

**The discovery problem, stated plainly (this is why §7 exists):** the system can only
text-search what the derivation *wrote down*. A description is a few sentences about a
picture containing thousands of details; the VLM writes what it considered important. Ask
later for "the photo with the small red connector" and text search finds nothing — the
description never mentioned it. **And raw access does not help, because access is not
discovery**: an agent can always open a file *it has found*, but it cannot decide to open a
file it never retrieved. Without this section, anything a description omits is invisible
forever. (Same for sound: transcripts capture speech; the alarm in the background exists in
no text.)

**The mechanism — one more P1 target, riding existing machinery (D8/D63 unchanged):**

- `search(channel=semantic, target=media_segments, query=<text | image | audio>, …)` —
  a **logical target over per-modality P1 tables/indexes**: one row per standalone image, per
  adaptive video keyframe/shot, per bounded audio segment; each row carries its **modality**
  (image | keyframe | acoustic), its **embedding family + version + dimension**, its
  `representation_id`, and its **immutable locator** (§4), hydrating to the representation
  passage + preview + raw deep link. Subindexes exist because no single model spans all
  modalities honestly: a CLIP-class model gives text↔image; audio↔text is a *different*
  embedding family with a different vector space and dimension — rows from different
  families are never compared by raw vector distance, only combined by rank (RRF).
- Embedding models are **port configuration** exactly like the text embedder (D63): versioned,
  per-deployment choices, one slot per modality pair. **Capability is advertised per
  (query modality → target modality) pair** — a deployment may support text→image but not
  audio→acoustic — and a query hitting an unconfigured pair gets D49's typed `boundary`
  naming exactly that pair and the workaround (text search over derivations still works).
  Configuration absence, never design absence, never a silent gap or a silent all-or-nothing.
- Results fuse with the text channels through the existing RRF operator; **zero LLM calls on
  the query path** (D9 holds — embedding lookup, same as text semantic search). When derived
  text and pixels disagree (the caption says "blue car", the visual match says otherwise),
  both candidates return with channel labels — fusion never synthesizes agreement; the agent
  audits raw when it matters.
- Rebuildable projection like all of P1; eval measures each configured pair separately
  (text→image, image→image, text→acoustic, text→shot) — they are different capabilities.

## 8. What P3 and the mounts show

Media documents are ordinary lineages in the corpus tree: a **stub** whose frontmatter
carries — beyond the standard `doc_id`/`artifact_uri`/`content_hash`/`section_path`
(e0 §5) — the **`raw_uri`** (mount-relative path to the original) and, for time-coded media,
the document's duration and preview links into the artifact `media/` folder
(keyframes/thumbnails), so the browse path shows what the file *is* before anyone opens
2 GB. Never whole raw media in the tree; never per-keyframe pseudo-documents. The raw mount
serves originals as bound in D51 (off-path, explicit pointers, audit-logged, mime-routed
storage classes — media likely to be read sits in standard/nearline, §e0).

**What a deep link *is* on each surface — stated so no one ships a broken promise.** The
rendered form `original.mp3#t=873` is a **media-fragment rendering for display**: browsers
and players understand it; a filesystem does not. So:

- **Mounted**: the stub/frontmatter/envelope carries the mount-relative raw path **plus the
  structured locator** (`start_ms`/`end_ms`/region). The consumption skill teaches the seek
  motion explicitly: open the mounted file with local tooling at the offset (any player's
  seek, `ffmpeg -ss 873 -i <mounted path> …` for a clip) — the fragment string is never
  itself a path.
- **Unmounted**: the locator goes to the serving operation (`hydrate depth=bytes` with a
  locator, retrieval §3), which returns a seekable, codec-aware segment for the interval or
  region — parity with the mounted seek, without downloading the file.

## 9. Decision interactions

| Decision | Effect |
|---|---|
| D38/D57 | **refined**: converter contract generalizes (source map, derived assets, manifest); routes added; canonical-text rule (document.md, sidecars are interchange) fixes the e0 §2 transcript-placement ambiguity |
| D51 | **confirmed and completed**: the raw mount + `media/` derived-only rule was the right half; locators + deep links complete the requirement's "agent gets raw when needed" with second-precision |
| D32 | **extended**: two-hop grounding; modality-aware layer-4 audits |
| D54–D56 | **precision fix + one new object**: representations become identified immutable objects (`document_representations`, representation-addressed artifact paths, current-pointer swap on completion); the extraction basis is `(representation_id, blockizer_version, structurer_version, extractor_version)`; upgrades flow the processing-driven ruleset; D56 reuse and `chunk_claims` occurrence provenance become representation-aware |
| D59 | **served**: diarization is what makes recorded stance attributable; conservative resolution protects it |
| D9/D63/D94 | **composes**: media embeddings are one more PostgreSQL P1 target + one more port config; zero-LLM query path holds |
| D49 | **extended**: envelope provenance carries locators + derivation disclosure; missing media channel is a typed `boundary` |
| D42 | **composes**: derivation-family provenance kept visible for future independence/confidence math |

## 10. Spikes (measure before locking; merged list from both analyses)

1. **Route-quality golden corpus** — WER/diarization-error/speaker-attribution-precision/OCR
   accuracy/VLM factuality/time-region alignment, multilingual incl. Czech; overlapping
   speech, screen recordings, charts, music, corrupt tracks.
2. **Grounding precision** — word vs segment timestamps: how often does the interval suffice
   for a quick audit; how often do claims need multiple locators.
3. **Modality-aware audit policy** — sampling rates and escalation bands per modality.
4. **Representation-lifecycle drills** — upgrades that (a) change text, (b) change only
   timestamps/speakers, (c) are identical: verify basis swap, reuse, `support_withdrawn`,
   `claims_as_of`, K triggers.
5. **Transcript/video structure quality** — PageIndex boundaries over long recordings.
6. **Description granularity** — whole-image vs region captions vs OCR-first: claim recall vs
   hallucination rate.
7. **Video coverage policy** — shot detection + adaptive sampling vs transient-event recall
   and index size; keyframe-count knobs.
8. **Direct media search recall** per task, sized against what text-over-derivations misses.
9. **Seek & parity** — codec-aware serving, gcsfuse reads, storage-class cost on large files;
   S59 must pass without downloading gigabytes to inspect ten seconds.
10. **Cost & retention** — per-hour ASR/VLM/embedding spend; representation-generation
    growth; hard-forget latency at target scale.
11. **Provider/privacy routes** — which adapters run locally vs send media to a provider;
    the manifest records the execution context (D61 ports).
12. **Image discriminator accuracy** (document vs picture) and misroute cost.
13. **S58 media extension** — a cold agent must distinguish source expression / model
    observation / media time / world time / current fact from the skill alone.
## References

Research: `plan/analysis/media_handling/` (internal, Codex, SYNTHESIS). Decisions: **D65**
(this design), D8, D9, D32, D38, D41, D42, D49, D51, D54–D57, D59, D61, D63.
Cross-edited designs: `e0_files_design.md` §2–§3, `e1_chunks_design.md` §2,
`evidence_lifecycle_design.md` §1/§3, `e2_e3_claims_relations_design.md` §3.3,
`retrieval_design.md` §3/§5/§8. Scenarios: S56, S59 (strengthened), S62–S63 (new).
Eval checks: `plan/implementation_evals/eval_checks/media_*.yaml`.
