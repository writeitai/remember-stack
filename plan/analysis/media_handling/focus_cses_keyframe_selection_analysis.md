# FOCUS & CSES — efficient keyframe selection for long video / video RAG

*2026-09-08. Analysis, non-binding. Captures two 2026 query-time keyframe
selectors (FOCUS, CSES) relevant to media ingest and retrieval cost —
especially avoiding “score every frame at 1 fps.” Evidence: paper abstracts /
arXiv pages and public GitHub READMEs as of this date; not a RememberStack
binding design.*

## The question being decided

For video (and long multimodal) memory, **how do we keep only frames that
matter without paying a full vision pass on every second of every asset?**

RememberStack’s media direction (D51 / `media_handling` synthesis) already
prefers **derived text first** (ASR, description, OCR) and treats
`media_segments` as a P1 target keyed to immutable locators — not a parallel
plane. Adaptive keyframing is already named as policy (“not every frame;
coverage reports”). FOCUS and CSES are concrete, published mechanisms for the
**query-time / long-span** half of that cost story.

## Split that matters

| Tier | When | Deterministic? | Cost shape |
| --- | --- | --- | --- |
| **A – Index / ingest** | Once per asset | Prefer yes (shots, SSIM/hist, clustering) | CPU / light CNN; no per-query VLM |
| **B – Query-time on retrieved span** | After text/locator retrieval narrows the video | Training-free greedy or bandit | Score a **budgeted** subset of frames |

FOCUS and CSES sit in **B**. They do **not** replace cheap deterministic
ingest; they stop flat 1 fps CLIP/VLM scoring when a hit is still minutes or
hours long.

---

## FOCUS (ICLR 2026)

**Title:** FOCUS: Efficient Keyframe Selection for Long Video Understanding  
**Venue:** ICLR 2026  
**arXiv:** https://arxiv.org/abs/2510.27280  
**Code:** https://github.com/NUS-HPC-AI-Lab/FOCUS  

### Mechanism (short)

1. Partition the video into short temporal **clips** (bandit arms).
2. Treat keyframe selection as **combinatorial pure-exploration**: allocate
   scoring budget to arms that are promising (high empirical relevance) or
   uncertain (large Bernstein-style confidence radius).
3. Reduce the sequential policy to a **coarse-to-fine two-stage** procedure
   (explore regions → pick top-scoring frames inside selected clips).

Training-free, model-agnostic, plug-and-play under a strict token budget.

### Cost / accuracy claims (from authors)

- Processes **&lt;2% of frames** while improving long-video QA accuracy.
- Large gains on long videos (authors report an **11.9%** accuracy lift on
  LongVideoBench for videos **&gt;20 minutes**, relative to their baselines).

### Determinism caveat

Exploration is **stochastic / optimism-driven**, not pixel-diff deterministic.
Reproducibility needs fixed seeds and fixed scorer; it is still far cheaper
than scoring every 1s frame.

### Fit for RememberStack

Use **after** text/hybrid retrieval (or ASR-anchored locator hits) has
narrowed to a long media span — e.g. “open the right 40 minutes, then FOCUS
inside.” Do **not** run FOCUS over the whole corpus at ingest.

---

## CSES (2026)

**Title:** Coverage-Driven Adaptive Keyframe Selection for Video Understanding  
**arXiv:** https://arxiv.org/abs/2608.00714  
**(Authors name the method CSES in the paper body.)**

### Mechanism (short)

1. Estimate how **prominent / concentrated** the frame–query relevance
   profile is, to decide how aggressively to acquire scores.
2. **Actively acquire** scores on unscored frames near high-relevance or
   under-covered temporal regions (expected marginal coverage gain).
3. Cast final selection as a **coverage** objective over semantic relevance,
   temporal redundancy, and visual redundancy.
4. Objective is **monotone submodular** → greedy selection with a standard
   approximation guarantee; stop when coverage **saturates**.

Training-free; adapts both **#frames scored** and **#keyframes kept** per
video–query pair.

### Cost / accuracy claims (from authors)

- Scores **~13–44× fewer** frames than baselines that score large fixed pools.
- Selects **~18–20% fewer** input keyframes while preserving accuracy across
  four LVLMs on two benchmarks.
- **~3.1–5.4×** faster frame selection than those baselines.

### Determinism caveat

Greedy coverage given scores is deterministic; the active-acquisition path
depends on the relevance estimator and stopping thresholds. Still designed
explicitly to **avoid scoring hundreds/thousands of frames** by default.

### Fit for RememberStack

Same tier as FOCUS: **budgeted query-time** selection on a retrieved media
window. Prefer CSES when you want an explicit **coverage saturation** stop
(clear “enough frames” signal for metering / cost export). Prefer FOCUS when
the window is extreme-length and you want bandit-style region discovery with
&lt;2% frame visits.

---

## Practical recommendation (non-binding)

Aligned with existing media synthesis (“text search cheap-first”; adaptive
keyframes; locator-bearing `media_segments`):

1. **Ingest:** sparse decode → shot/scene or SSIM/hist redundancy cut → few
   keyframes + Whisper/OCR into `document.md` + source map. Optional small
   vision embed only on survivors for `media_segments`.
2. **Retrieve:** text / hybrid / media_segments as today — never “score all
   frames in the corpus.”
3. **Deepen only on hits:** if the agent opens a long video span and needs
   more visual evidence than stored keyframes, run **FOCUS or CSES** (or a
   simpler AKS/AdaRD-style greedy) **only inside that span**, under a hard
   scoring budget tied to request-path metering.

### Explicit non-goals of this note

- Not choosing FOCUS vs CSES as binding.
- Not specifying scorer model, clip length, or saturation thresholds.
- Not proposing per-keyframe pseudo-documents (already rejected in media
  synthesis: P3 holds stubs/previews, not one doc per frame).

## Related neighbors (pointers only)

| Paper | Role | Link |
| --- | --- | --- |
| AKS (CVPR 2025) | Query relevance + temporal coverage bins | https://arxiv.org/abs/2502.21271 |
| AdaRD-Key (2025) | Relevance–diversity MaxVol; often assumes 1 fps pool | https://arxiv.org/abs/2510.02778 |
| LENS (ECCV 2026) | Spatial zoom-in / temporal zoom-out keyframe sampling | https://arxiv.org/abs/2607.25125 |
| QCA (2026) | Segment budget by query + content deviation | https://arxiv.org/abs/2607.00983 |

## Sources

- FOCUS arXiv / GitHub / ICLR 2026 listing (links above).
- CSES arXiv HTML (https://arxiv.org/html/2608.00714).
- Internal context: `plan/analysis/media_handling/SYNTHESIS.md` (adaptive
  keyframe policy; text-first; `media_segments`).
