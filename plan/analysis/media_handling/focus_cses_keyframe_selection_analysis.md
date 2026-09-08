# Efficient video frame selection for RAG / retrieval — literature capture

*2026-09-08. Analysis, non-binding. Survey of papers and repos on selecting
frames that matter for video RAG and long-video understanding — especially
avoiding “score every frame at 1 fps.” Deep dives: FOCUS (ICLR 2026) and
CSES (2026). Broader catalog from the same research pass. Evidence: arXiv /
GitHub / venue pages as of this date; not a RememberStack binding design.*

## The question being decided

For video (and long multimodal) memory, **how do we keep only frames that
matter without paying a full vision pass on every second of every asset?**

RememberStack’s media direction (D51 / `media_handling` synthesis) already
prefers **derived text first** (ASR, description, OCR) and treats
`media_segments` as a P1 target keyed to immutable locators — not a parallel
plane. Adaptive keyframing is already named as policy (“not every frame;
coverage reports”). This note captures concrete published mechanisms for
both **cheap ingest** and **budgeted query-time** selection.

## Split that matters

| Tier | When | Deterministic? | Cost shape | Examples |
| --- | --- | --- | --- | --- |
| **A – Index / ingest** | Once per asset | Prefer yes (shots, SSIM/hist, clustering) | CPU / light CNN; no per-query VLM | PySceneDetect, TransNetV2, LMSKE, MaxInfo |
| **B – Sparse embed diversity** | On ingest survivors | Mostly yes (fixed k / seed) | Embed candidates only | VideoRAG k-means++, MaxVol / FPS |
| **C – Query-time on retrieved span** | After retrieval narrows the video | Training-free greedy or bandit | Score a **budgeted** subset | FOCUS, CSES, AKS, AdaRD-Key, LENS, QCA |
| **D – Corpus RAG architecture** | System design | N/A | Dual text+visual index; scene chunks | Video-RAG Luo, VideoRAG HKUDS, SceneRAG, iRAG, Goldfish |

FOCUS and CSES sit in **C**. They do **not** replace cheap deterministic
ingest; they stop flat 1 fps CLIP/VLM scoring when a hit is still minutes or
hours long.

---

## Deep dive: FOCUS (ICLR 2026)

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

## Deep dive: CSES (2026)

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

## Catalog: ingest / deterministic & sparse diversity (tiers A–B)

### LMSKE — TransNetV2 shots + adaptive clustering + hist dedupe (2024)

- **arXiv:** https://arxiv.org/abs/2401.04962  
- **Keyframe work / dataset:** https://github.com/ttharden/Keyframe-extraction  
- **TransNetV2:** https://github.com/soCzech/TransNetV2  
- **How:** shot boundaries → CLIP features within shot → adaptive clustering
  → HSV-histogram redundancy cut (~0.8) + solid-color drop → compact sequential
  keyframe list.  
- **Takeaway:** Best academic template for **query-agnostic indexing**:
  segment → cluster → hist-dedupe → store vectors + timestamps. Still embeds
  inside shots (not free), but no query scoring and far fewer embeds than
  full-video 1 fps if long shots are subsampled.

### TransNetV2 + PySceneDetect (production shot tools)

- **TransNet V2** (Souček & Lokoč, 2020): https://arxiv.org/abs/2008.04838 ·
  https://github.com/soCzech/TransNetV2 — dilated 3D CNN for hard/gradual cuts.  
- **PySceneDetect:** https://github.com/Breakthrough/PySceneDetect — HSV
  content / adaptive thresholding on CPU.  
- **Practice:** 1–3 frames per shot (start / mid / sharpest). Lectures/slides:
  ContentDetector often enough; film/dissolves: TransNetV2.  
- **Takeaway:** Default **first filter**. Do not index every second if shots
  already give natural chunks.

### SSIM / frame-diff / histogram keyframing

- **Example repo:** https://github.com/CaptnSeraph/SSIM-keyframe-extraction  
- Classic DiffHist-style absolute histogram difference (surveyed in LMSKE).  
- **How:** Compare consecutive or strided frames with SSIM / hist L1; emit a
  keyframe when cumulative change exceeds a threshold.  
- **Takeaway:** Fully deterministic OpenCV **pre-filter before any CLIP pass**
  (e.g. decode 2–5 fps → SSIM gate → only survivors get embeddings). Ideal for
  talking head / slides.

### VideoRAG (KAIST) — k-means++ frame-space reduction (ACL Findings 2025)

- **arXiv:** https://arxiv.org/abs/2501.05874  
- **Code:** https://github.com/starsuzi/VideoRAG  
- **How:** Often sample ~1 fps → CLIP → **k-means++** → keep centroid-nearest
  frames (e.g. 8→4 retrieval, 64→32 generation). Optional trained MLP scorer;
  clustering alone beats uniform. Whisper ASR when subtitles missing.  
- **Takeaway:** “CLIP on sparse candidates → k-means++ / k-medoids → store
  medoids” is validated. **Pair with shot/SSIM first** so ingest does not
  start from raw 1 fps.

### MaxInfo — MaxVol diversity on embeddings (WACV 2026)

- **arXiv:** https://arxiv.org/abs/2502.03183  
- **Code:** https://github.com/FusionBrainLab/MaxInfo  
- **How:** Embed candidate matrix (CLIP/SigLIP); select subset maximizing
  geometric **volume** (rect MaxVol). Fast / slow / chunk-aware variants.
  Query-agnostic diversity alternative to uniform.  
- **Takeaway:** Prefer over uniform when sparse embeddings already exist and
  you want **coverage without a query**. Same family as farthest-point /
  log-det diversity.

---

## Catalog: other query-time selectors (tier C)

### AdaRD-Key — relevance + log-det diversity (2025)

- **arXiv:** https://arxiv.org/abs/2510.02778  
- **Code:** https://github.com/Xian867/AdaRD-Key  
- BLIP-2 relevance + greedy Max-Volume diversity; VB-Scale adapts λ; falls
  back to diversity-only if max relevance is weak. Training-free, but
  **default candidate pool is often 1 fps** — expensive at corpus scale. Best
  as query-time selector on a **shot-filtered** segment.

### AKS — Adaptive Keyframe Sampling (CVPR 2025)

- **arXiv:** https://arxiv.org/abs/2502.21271  
- **Code:** https://github.com/ncTimTang/AKS  
- VL relevance to prompt + recursive binning for **timeline coverage**;
  adaptive tradeoff. Training-free greedy; still scores candidates with a VL
  model. Good query-time baseline (AdaRD papers often compare against it).

### LENS — adaptive spatio-temporal zooming (ECCV 2026)

- **arXiv:** https://arxiv.org/abs/2607.25125  
- **Code:** https://github.com/zhangce01/LENS  
- Training-free: allocate frame budget between **spatial zoom-ins**
  (query-relevant regions within frames) and **temporal zoom-outs**
  (multi-frame aggregation). Improves long-form Video-MME vs uniform and prior
  keyframe methods.

### QCA — Query- and Content-Aware keyframe selection (2026)

- **arXiv:** https://arxiv.org/abs/2607.00983  
- Partition into segments; estimate information contribution via query
  relevance + content deviation; **dynamically allocate keyframe budget** per
  segment; intra-segment selection balances relevance and diversity.
  Training-free; plug-and-play with Video-LLMs. Code claimed on GitHub in
  paper (verify at fetch time).

### Q-Gate — query-modulated multimodal keyframe selection (2026)

- **arXiv:** https://arxiv.org/abs/2604.17422  
- Training-free modality routing: Visual Grounding / Global Matching /
  Contextual Alignment (subtitles); LLM query-modulated gating allocates
  expert weights. Addresses “visual-only fails narrative queries; always-on
  text adds modal noise.”

### Secondary pointers

| Paper | Role | Link |
| --- | --- | --- |
| Q-Frame | CLIP scores + Gumbel-Max sampling + multi-res (query-aware; not fully deterministic) | https://arxiv.org/abs/2506.22139 |
| mDP3 | DPP + MDP for listwise diversity / sequentiality (training-free, heavier math) | https://arxiv.org/abs/2501.02885 |

---

## Catalog: video RAG / corpus pipelines (tier D)

### VideoRAG (HKUDS) — dual-channel long-video corpus RAG (2025)

- **arXiv:** https://arxiv.org/abs/2502.01549  
- **Code:** https://github.com/HKUDS/VideoRAG  
- Graph-based textual grounding + multimodal visual encoding over
  extreme-length / multi-video corpora (LongerVideos ~134h). Blueprint for
  **corpus-level** video RAG more than a novel keyframer.

### SceneRAG — narrative scenes instead of fixed chunks (2025)

- **arXiv:** https://arxiv.org/abs/2506.07600  
- LLM + ASR (+ silence heuristics) → narrative scenes; multimodal KG;
  retrieve scenes not fixed windows. Beats fixed-length chunk RAG on
  LongerVideos. Complements TransNetV2 / PySceneDetect for chunking unit.

### Video-RAG (Luo et al.) — auxiliary text RAG, not denser frames (2024/25)

- **arXiv:** https://arxiv.org/abs/2411.13093  
- **Code:** https://github.com/Leon1207/Video-RAG-master  
- Keep a **small** uniform frame set; retrieve ASR/OCR/detection snippets via
  Contriever+FAISS; inject as text. Large gains **without** sampling more
  frames. Often cheaper and better: dual-index **ASR + OCR + few keyframes**.

### iRAG — incremental / on-demand detail extraction (CIKM 2024)

- **arXiv:** https://arxiv.org/abs/2404.12309  
- Cheap upfront index; heavy vision/text extraction only on
  **query-selected** segments (authors report ~23–25× faster ingest).
  Architectural twin of “don’t pay full vision cost until retrieval says
  where.”

### Goldfish — clip captions → retrieve top-k → answer (ECCV 2024)

- **arXiv:** https://arxiv.org/abs/2407.12679  
- **Site:** https://vision-cair.github.io/Goldfish_website/  
- Segment → MiniGPT4-Video captions → text retrieval of top-k clips → LLM.
  Scales via RAG, not denser frames. Still needs good clip boundaries.

### CARVE — rethinking RAG in long videos (2026)

- **arXiv:** https://arxiv.org/abs/2606.13141  
- Chunk-adaptive retrieval/generation (what to retrieve and how to use it);
  frame-level keyframes via **k-means++** inside chunks (e.g. n=5 centroids).
  Query/chunk-aware modality and granularity choices rather than a single
  global frame policy.

---

## Practical recommendation (non-binding)

Aligned with existing media synthesis (“text search cheap-first”; adaptive
keyframes; locator-bearing `media_segments`):

1. **Ingest (tier A→B):** sparse decode (2–5 fps or demux keyframes) →
   PySceneDetect / TransNetV2 → SSIM/hist redundancy cut → optional
   k-medoids / MaxVol on survivors → Whisper/OCR into `document.md` + source
   map; optional small vision embed for `media_segments`.  
2. **Retrieve (tier D habits):** text / hybrid / media_segments first —
   never “score all frames in the corpus.” Prefer **scene/shot** chunks over
   fixed N-second slabs (SceneRAG evidence).  
3. **Deepen only on hits (tier C):** if the agent opens a long video span and
   needs more visual evidence than stored keyframes, run **FOCUS or CSES**
   (or AKS / AdaRD / LENS / QCA) **only inside that span**, under a hard
   scoring budget tied to request-path metering.

### Minimal “v0” stack (engineering sketch)

`ffmpeg` sparse decode → `PySceneDetect` → 1–2 frames/shot → Whisper + CLIP →
dual index → retrieve → optional FOCUS/CSES/AKS on top hits.

### Explicit non-goals of this note

- Not choosing FOCUS vs CSES (or any single paper) as binding.
- Not specifying scorer model, clip length, or saturation thresholds.
- Not proposing per-keyframe pseudo-documents (already rejected in media
  synthesis: P3 holds stubs/previews, not one doc per frame).
- Not endorsing uniform **1 fps → CLIP everything → store all** at ingest.

## Sources

- FOCUS arXiv / GitHub / ICLR 2026 listing (links above).
- CSES arXiv HTML (https://arxiv.org/html/2608.00714).
- Remaining entries: linked arXiv / GitHub / project pages in each section
  (verified in the research pass of 2026-09-08; re-check before binding).
- Internal context: `plan/analysis/media_handling/SYNTHESIS.md` (adaptive
  keyframe policy; text-first; `media_segments`).
