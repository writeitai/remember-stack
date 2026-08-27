# Phase 1 — Walking Skeleton

**Goal:** one document, end to end, everything minimal: bytes → blocks → chunks → claims →
facts → P1 → a queryable answer with a correct envelope. Proves spine integration, the grain
discipline, and propose/dispose before any layer gets deep.

**Entry gates (both closed):** #3 embedding model → **D63** (conventional + prefix binds,
e1 §5); #4 extractor model → **D70** (`gpt-5.6-luna` port default).
**Exit criteria:** a toy corpus (≈10 mixed docs) ingests; scenario classes **S1, S2, S5, S39**
pass; the grain CI invariants hold (fact vs evidence labeling; claims never answer
current-fact); propose/dispose verified (a hand-invalidated fact never surfaces as current).
Deliberately absent here: ER beyond T0, supersession cascade, versions, K, live graph/P3.

| WP | Goal | Reads | Depends | Deliverable | Acceptance | Status |
|---|---|---|---|---|---|---|
| WP-1.1 | Minimal E0: upload connector, one convert route (markitdown), blockizer, synthetic-root structure, artifacts layout | e0 §1–5; e1 §2 (D57); D36–D38 | Phase 0 | E0 worker chain | doc → document.md + blocks.json + rows; blockizer corpus green | done — PR #81: upload connector (content-derived lineage, D55 no-op on identical bytes), D38 conversion router (passthrough + markitdown), convert handler writing ID-addressed artifacts + immutable representations (D65 replay-not-regenerate on retry), synthetic-root structure with the D54 completion flip; end-to-end proofs against real PostgreSQL |
| WP-1.2 | E1: block-packing chunker (section-bounded, anchors per e1 §4) + embeddings (+ prefix stage iff #3 branch says so) | e1 §4–§5, §7 keys; D58 | WP-1.1 | chunk worker + P1 chunk table | deterministic repack; chunks in Lance | done — PR #82: anchor-stabilized whole-block packing (leaf sections only, params-derived generation), D56 reuse keys with no LLM output, conventional-mode context prefixes with D7 replay, one embed batch per document into the P1 Lance table |
| WP-1.3 | E2 minimal: two-call Claimify (Selection incl. D59 attributed-stance keep; decontextualize/decompose/ground), grounding gate, decision ledger | e2_e3 §2–4; D31–D35, D59; schema §8 | WP-1.2 | extractor worker | grounding CHECK holds; drops ledgered; stance kept on sample | done — PR #83: two-call Claimify over the D31 bundle with the deterministic D32 gate (anchor + window membership + enforced Selection), D33 ledger incl. empty-terminal markers and kept_flagged pairing, D59 stances attributed, OpenRouter adapter (D70 default) |
| WP-1.4 | E3 minimal: T0-only resolution, mentions, novelty gate, relations + observations insert, evidence links + counts (D54 rule from day one) | e2_e3 §5; observations §2–3 (blocking only); registries §2 (tables); D54 counting | WP-1.3 | normalizer worker | same fact twice = one row, count=1 (lineage-distinct) | done — PR #84: T0 resolution (active-only, advisory-lock serialized mints) with mentions + append-only verdicts, normalizer with registry/signature gates on resolved stored types, D2 collapse with evidence-once links and lineage-distinct D54 recounts, D43 observations via the novelty gate |
| WP-1.5 | P1 inline writes: claims + fact labels (+ role scalar) | retrieval §5 (P1 policy); D8 | WP-1.3 | Lance tables + writers | current-testimony-only default channel | done — PR #85: P1 claims channel (is_current_testimony default-filter scalar) + labeled facts channel in Lance, stamp-after-index-write ordering, doc-scoped lock-serialized label sweeps, embedding-model-scoped generations |
| WP-1.6 | Retrieval core: `resolve` (T0), `lookup`, `search`, `hydrate` + propose/dispose + minimal envelope (grain, validity, freshness stamps, negatives) | retrieval §2–3, §5–6; D48–D49 | WP-1.4, WP-1.5 | HTTP API | S1/S2/S5/S39 green; drop-count honesty test | done — PR #86: zero-LLM QueryEngine (T0 resolve with S60 redirect-following, both-clocks current-fact lookups, semantic observation lookup, claims search, S5 hydration chain), D48 nominate-then-drop with honest drop counts, minimal D49 envelope with the typed negative taxonomy, FastAPI surface |
| WP-1.7 | Skeleton eval pack: wire the S-subset + grain contract tests into the harness | retrieval §11; D22 | WP-1.6, WP-0.5 | eval suite `skeleton` | suite green in CI | done — PR #87: the S-subset (S1/S2/S5/S39) + grain contract as retrieval-suite canaries with a QueryEngine-replaying evaluator; green over the walking-skeleton corpus, fails on a broken corpus (CI-blocking) |

**Phase 1 exit: met (2026-07-18).** The toy chain ingests end to end (upload →
convert → structure → chunk → embed → extract → normalize → P1 writes); S1,
S2, S5, and S39 pass over the HTTP API and as harness canaries; the grain CI
invariants hold (claims answers are evidence grain and never answer
current-fact; fact lookups filter both clocks); propose/dispose is verified
(a hand-invalidated fact never surfaces as current — the drop-count honesty
and both-clocks tests). Deliberately absent per the phase goal: ER beyond T0,
supersession cascade, versions, K, live graph/P3.
