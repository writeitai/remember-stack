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
>   the **raw mount** (D51/D108) serves immutable originals read-only, on the navigation path
>   at `<doc_id>/<content_hash>/original.<ext>` — above the representation directory, so every
>   representation of the same bytes shares one copy; `media/` in the artifacts bucket holds
>   *derived* media; facts count **distinct source lineages** (D54).

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
| **Image** (`image/*`) | **both lanes, weighted**: VLM description and OCR of visible text, with a classifier setting *emphasis and budget*, never exclusion | the description and a "Visible text (OCR)" section, clearly sectioned; region-grain descriptions permitted as sub-sections with image-region locators | the **agent rendition** (§4a) plus any crops/thumbnails |

Notes an implementer needs:

- **The image classifier chooses emphasis, not exclusion.** MIME cannot distinguish a
  scanned page from a photo, so the route includes a cheap classifier (or the VLM's own
  routing call) — but its output is a *weighting*, not a switch. A scan-leaning image spends
  its budget on OCR and still records a short visual description; a photo-leaning image
  spends it on description and still runs OCR over any visible text. The reason is that the
  classes are not disjoint in practice: a screenshot, a chart, a slide, and a whiteboard
  photo all carry both readable symbols and visual structure that the other lane would
  discard. An exclusive switch turns every misclassification into permanently missing
  evidence; a weighting turns it into a cheaper-but-complete conversion. Both lanes label
  their ranges under §5, so a reader always sees which lane produced which text, and a lane
  that genuinely produced nothing is disclosed as a coverage gap rather than omitted.
  Misroutes remain recoverable either way — every route is a versioned conversion of
  immutable bytes, and a re-weighting is a version bump (§6).
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
  **locator-aware serving operation** returning a seekable, codec-aware segment — a naive
  byte-range is a false promise for arbitrary video codecs. Two named operations share that
  one serving path: `hydrate depth=bytes` fetches the *bytes* of a locator's interval or
  region, and **`source_open` (§4a)** delivers the same material as *content in the client's
  perceptual channels* — the operation an agent uses when it needs to look rather than to hold
  bytes. Clip extraction is a
  *serving* operation, never a new stored artifact.
- **Three kinds of time, named apart** (schemas, API fields, the consumption skill):
  `start_ms` = where in the *file* the evidence occurs; `claim_valid_from` (D41) = when the
  fact held *in the world*; `ingested_at` = when the *system* learned it. Calling any two of
  these "the timestamp" invites wrong as-of queries.

## 4a. Agent-visible source access — `source_open` serves perception, not pointers

**The problem this solves.** §4 gives a claim a precise pointer into its source: this region
of this image, this interval of this recording. The pointer is only useful if the agent can
*act* on it. A mounted agent can — open the file, seek, look. For an unmounted agent every
existing answer stops one step short: a `raw_uri` is a string, a URL is a promise the client
may never redeem, and `hydrate depth=bytes` returns *bytes*, which a model cannot perceive
unless something decodes them into its input channel. Because §4's second grounding hop
exists so a reader can catch the converter being wrong, a serving path that never puts the
source in front of the model reduces that audit to paraphrasing the converter's own output.

**The operation.** `source_open` is the locator-aware operation that serves a source as
**perceptual content** — content in the client's native modality channels rather than a
reference to be resolved later. It is a direct §3 primitive (retrieval), not a fifth assured
operation (D87 unchanged), and it shares `hydrate depth=bytes`'s resolution path,
authorization, and audit. It carries its own name because "let me look at it" is a different
agent intent from progressive record deepening, and tool discovery is how agents find intents.

```
source_open(version_id, representation_id?, locator?, accept?) -> envelope(grain: evidence)
```

There is **no caller-selected view mode.** The server decides whether the stored original can
be served as-is — already a supported format, safely decodable, within served bounds — or
whether a rendition must stand in, and the result says which it did (below). Making that a
caller flag would invite an agent to request an unbounded original into its own context, and
would let two callers disagree about what "the source" means.

**What each modality returns.** "Perceptual" is modality-specific, and every media kind the
router accepts has a defined answer:

| Source | Content returned | With a locator |
|---|---|---|
| Image | image content, in a format the client accepts | whole-image overview **plus** a high-detail crop of the region |
| Audio | audio content for the interval | the interval, bounded by served limits |
| Video | keyframe image content for the interval plus its audio | the interval's frames and audio, not the whole file |
| Pageless text sources | the source text of the `source_range` | the interval, with surrounding context |

A region or interval always returns orienting context alongside the detail, because a crop
without context is uninterpretable and an overview without detail loses what motivated the call.
What "context" is differs by modality, and each is bounded: for an image it is the whole-image
overview beside the crop; for a recording it is **not** a longer excerpt — extending audio to
provide context has no natural stopping point and would defeat the served duration bound — but
the document's derived preview and summary material (§8) carried beside the requested interval.
A caller that wants more of a recording asks for a wider interval, which is a locator it can
state and a cost it can see.

**Without a locator, a time-based source returns its preview material, never an excerpt.** An
image has a natural whole — the image — so a locator-free call returns it. A recording does
not: any excerpt the server picked would be an arbitrary claim about which ten seconds
mattered, and the whole file is never inlined. A locator-free call on `audio/*` or `video/*`
therefore returns the document's existing derived preview material (keyframes, thumbnails, and
the derived summary sections — §8) plus the handle and duration, and no audio or video content.
Perceiving a recording requires saying *when*; that is what §4's locators are for.

**What the contract does and does not guarantee.** It guarantees the *server* delivered the
source in a form the client's model can consume, in the same response as the identity that
proves what it is. That closes the server-side failure mode, and it is strictly stronger than
a link: a `resource_link` or signed URL delivers nothing, and the protocol does not require a
client to fetch it, so a link-only result is indistinguishable server-side between "the agent
looked" and "the agent did not". It does **not** prove the pixels entered the model's context.
MCP defines image and audio blocks as tool-result content and leaves it to the host how those
blocks reach the model, so no server-side success can establish that a host forwarded them.
The audit record is therefore honest about its own scope: it records that content of a stated
kind, size, and hash was delivered to a named principal, never that a model perceived it.
**End-to-end perception is proved by evaluation, not by the wire contract** — the held-out
detail check in §10's spike list, where an agent must report a visual or audible detail
deliberately absent from every derived text. A wire contract can make perception possible and
remove every server-side excuse; only a test can show it happened.

**Format negotiation is caller-declared, because no protocol declares it.** MCP has no
standard client capability announcing which tool-result image or audio MIME types a host
accepts, so a server cannot infer it. The caller therefore states it: `accept` is an optional
list of MIME types the caller can consume. The deployment publishes its **served set** —
output formats per modality with maximum payload, pixel, and duration bounds — as ordinary
capability data in the envelope, the way D49 carries capability and freshness. The server
returns the best match between `accept` and the served set; with `accept` omitted it returns
the served default, chosen to be the most broadly supported member. **An empty intersection is
a typed `boundary` (D49)** naming the served set, never a silent failure or a payload the
caller cannot decode. An agent must never have to invent a `max_edge_px` to look at a photo:
bounds are served, not caller homework.

**The response maps content to identity, part by part.** One response may carry several
content blocks — an overview and a detail crop, or a video interval's keyframes and its audio.
The envelope therefore carries a `content_manifest[]` in the same order as the protocol's
native content blocks — a name chosen deliberately, because D87 removed `Envelope.parts` as an
envelope-of-envelopes composition mechanism and this is not that: it describes one response's
own content blocks and composes nothing — each entry naming its `role` (`overview` | `detail` | `keyframe` | `audio` |
`source_text`), `content_kind` and `mime`, its `bytes_sha256`, its `origin` (`original` |
`agent_rendition`), the `transforms[]` that produced it when derived, the `locator` it
answers, and its `trust: untrusted` label. Without that pairing a reader holding three images
cannot say which is the crop, which is byte-identical, and which was resampled — and the
identity guarantee below would be unverifiable in exactly the case it matters most.

**Original versus rendition is never blurred.** Every result declares its content `original`
or `agent_rendition`. `original` means byte-identical to the stored source and hash-verifiable
against it. `agent_rendition` means a derived view, and it carries its own hash plus the
transform list that produced it — decoder and version, orientation applied, colour conversion,
resampling, and what metadata was removed. Renditions exist because many clients cannot
consume HEIC, TIFF, camera RAW, active SVG, or a 100-megapixel panorama, and because handing a
model unsanitized source is a decode-safety hazard. Whole originals of any size remain
fetchable for audit, export, and hash verification through `hydrate depth=bytes` and the CLI
download path, which are byte channels with no context budget to protect.

**Where renditions come from — reads stay side-effect-free.** The route produces the standard
renditions at conversion time, as ordinary `media/` derived assets under §1 with locators and
manifest entries like any other. `source_open` serves those. When a locator names a region or
interval with no stored asset, the operation performs the same **ephemeral** transform that §4
already binds for clip extraction ("clip extraction is a *serving* operation, never a new
stored artifact"; retrieval §7 repeats it): it computes the view, returns it, and stores
nothing.
Retrieval §12's rule that reads never write is preserved exactly — a read may compute, but
only a conversion creates a stored asset, and only §6 advances a representation.

**Retrieval offers the action; it does not take it.** Every media-bearing envelope item
carries a compact **source handle** in its provenance block (§5 of retrieval): immutable
identity, detected MIME, dimensions or duration, the readiness dimensions of §4b, any region
or interval locator, and `source_open` named as the next action — and no content. The agent
decides whether the question is worth the context and the bandwidth. The system must neither
inline every source into every answer (burning context, widening prompt-injection surface, and
removing the agent's judgment) nor bury the original behind a surface only a human can drive
(removing its autonomy). This is D51's "compact by default, the source one decision away"
applied to the moment of looking. **A mounted agent does not need this operation at all** — under
D108 it opens `<doc_id>/<content_hash>/original.<ext>` directly, which is the better motion
whenever the filesystem is available. `source_open` exists for the unmounted agent, which is
most agents against a managed deployment, and for locator-scoped access to large media where
the file is present but nobody wants all of it in context.

**The source is untrusted evidence.** Decoding runs in an isolated, resource-bounded process
with pixel, frame, recursion, and time limits, so malformed files and decompression bombs fail
before any expensive model runs. An opened source can itself carry an injection — text in the
image, speech in the recording, addressing the agent directly. The result labels its content
untrusted, and the consumption skill (retrieval §8) teaches the rule: visible or audible
instructions inside a source are *testimony to report*, never instructions to follow. EXIF and
similar metadata survive only in the original; a rendition's manifest records what was
stripped, and location or device identifiers are not promoted into retrieval by default. Every
open and every download is audited with principal, version, representation, rendition,
locator, byte count, content kind, and outcome.

## 4b. Scoped readiness — one boolean cannot describe a media document

A media document is not simply ready or not. Its source can be safely stored and openable
while its conversion failed; its text can be fully searchable while its visual index rebuilds.
Reporting one flag either lies about what works or withholds what does. The source handle
(§4a) and the envelope's provenance therefore carry four independent dimensions, each read
from state that already exists rather than from a new flag:

| Dimension | Read from | False means |
|---|---|---|
| `source_stored` | the version's content object and its verified hash | the ingest did not happen; nothing else is meaningful |
| `agent_view_ready` | the detected type resolves to a decoder in the deployment's served set (§4a), and the version passed safe-decode admission at ingest | `source_open` cannot serve perceptual content; `hydrate depth=bytes` may still serve bytes |
| `text_retrieval_ready` | a current representation (§6) exists and its projection is caught up | the document is invisible to text search; its source is still openable |
| `visual_search_ready` | **per query→target modality pair**, exactly as §7 advertises capability: its `media_segments` rows are current for that pair | that pair misses the document; other configured pairs and every non-search path still work |

`visual_search_ready` is therefore a map from pair to state, not one boolean: a source can be
discoverable by a text query and not by an image query, and collapsing the two would report a
capability the deployment does not have. An unconfigured pair is D49's typed `boundary`, not a
false value.

**Per-lane detail is disclosure, not a fifth flag.** Which *lane* failed — OCR, description,
diarization, keyframing — is already recorded where it belongs: the manifest's coverage policy,
coverage result, and gaps/warnings (§2), labeled per range under §5. A document whose OCR lane
produced nothing while its description lane succeeded has `text_retrieval_ready = true` and a
disclosed coverage gap naming the empty lane; a reader that needs the distinction reads the
manifest, which is the object that actually knows. Inventing per-lane booleans beside it would
create a second, drifting account of the same fact.

The rule this encodes: **a failure in one lane never removes access earned by another.** A
description-model outage must not make an already-safe source impossible to look at, and a
failed visual index must not withdraw text results. Recovery is per-lane and re-runs nothing
else: a rebuilt projection needs no re-conversion, and a re-run conversion lane advances a new
representation under §6 rather than mutating the current one.

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

**The mechanism — one more P1 target on accepted natural media rows (D63/D94):**

- `search(channel=semantic, target=media_segments, query=<text | image | audio>, …)` —
  a **logical target over accepted per-modality segment/representation rows and
  indexes**: one row per standalone image, per
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
2 GB. Never a *duplicated* original in the tree, and never per-keyframe pseudo-documents: the
one original is reachable in place at `<doc_id>/<content_hash>/original.<ext>`, above the
representation directory, so browsing finds it without any copy being made (D108). The raw
mount serves originals as bound in D51 (read-only, mime-routed storage classes — media likely to be read sits in standard/nearline, §e0).

**What a deep link *is* on each surface — stated so no one ships a broken promise.** The
rendered form `original.mp3#t=873` is a **media-fragment rendering for display**: browsers
and players understand it; a filesystem does not. So:

- **Mounted**: the stub/frontmatter/envelope carries the mount-relative raw path **plus the
  structured locator** (`start_ms`/`end_ms`/region). The consumption skill teaches the seek
  motion explicitly: open the mounted file with local tooling at the offset (any player's
  seek, `ffmpeg -ss 873 -i <mounted path> …` for a clip) — the fragment string is never
  itself a path.
- **Unmounted**: the locator goes to the serving operation — `hydrate depth=bytes` for a
  seekable, codec-aware segment of the interval or region (retrieval §3), or `source_open`
  (§4a) when the agent needs to *perceive* the source rather than hold its bytes. Parity with
  the mounted seek, without downloading the file.

## 9. Decision interactions

| Decision | Effect |
|---|---|
| D38/D57 | **refined**: converter contract generalizes (source map, derived assets, manifest); routes added; canonical-text rule (document.md, sidecars are interchange) fixes the e0 §2 transcript-placement ambiguity |
| D51 | **confirmed, completed, then amended**: the raw mount + `media/` derived-only rule was the right half; locators + deep links complete "agent gets raw when needed" with second-precision; **D108** withdraws the off-navigation-path clause so a browsing agent reaches the original directly |
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
12. **Image lane weighting**: classifier accuracy as an emphasis signal, and the cost of
    running the lighter lane at reduced budget versus the evidence it recovers (D107 — the
    discriminator no longer excludes a lane, so the measurement is spend-versus-recall, not
    misroute cost).
12b. **Held-out perceptual detail** (the D107 end-to-end check). Build a corpus where each
    media source contains a detail deliberately absent from every derived text — a visual
    element no description mentions, a sound no transcript renders. Ask an agent, through the
    ordinary retrieval path and then `source_open`, to report that detail. Caption-only and
    OCR-only paths must fail it; the source-open path must pass. This is the only measurement
    that shows perception happened end to end, because the wire contract can prove delivery
    and nothing more (§4a). Measure it per client, since host handling of content blocks is
    a client property, not a server one.
13. **S58 media extension** — a cold agent must distinguish source expression / model
    observation / media time / world time / current fact from the skill alone.
## References

Research: `plan/analysis/media_handling/` (internal, Codex, SYNTHESIS). Decisions: **D65**
(this design), D8, D9, D32, D38, D41, D42, D49, D51, D54–D57, D59, D61, D63.
Cross-edited designs: `e0_files_design.md` §2–§3, `e1_chunks_design.md` §2,
`evidence_lifecycle_design.md` §1/§3, `e2_e3_claims_relations_design.md` §3.3,
`retrieval_design.md` §3/§5/§8. Scenarios: S56, S59 (strengthened), S62–S63 (new).
Eval checks: `plan/implementation_evals/eval_checks/media_*.yaml`.
