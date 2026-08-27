# P3 Agent Navigation — Hierarchy, Index Files, and Directory Summaries (Analysis)

How agents should *find things* in the mounted corpus: whether the corpus filesystem should be
a fully materialized tree or only a set of navigable indexes, what the per-directory
`_index.md` files must contain, whether directories should carry their own summaries (beyond
PageIndex's per-file grain), and what all of this costs to build and maintain. The end goal it
optimizes for: **a coding-agent harness (Claude Code / Codex class) reaching the right
information in the fewest hops and fewest read tokens, using the filesystem skills it is
already exceptional at.** Verdict up front: the design already holds ~90% of the right answer
(D39/D40/D51); what is genuinely undecided is the **content contract of the index files**, the
**directory-summary mechanism**, and the **taxonomy/fan-out rules** — this analysis recommends
concrete answers for each.

## 1. What the system already holds (so we don't re-decide it)

- **Per-file understanding is E0's product (D39):** every document gets a PageIndex section
  tree with roles, spans, and **per-section summaries** — including a root-level summary of
  the whole document — persisted in `document_sections` (queryable) and `pageindex.json`
  (mountable). Summaries are *context, never facts*; the corpus-level picture is plane K's
  job (`e0_files_design.md` §4).
- **Virtual placement is emitted where understanding is fresh (D39):** the structurer proposes
  a path (`/finance/annual-reports/2023/`) as an **advisory hint**; the authoritative tree is
  reconciled later by the projection, because one document cannot know the global tree.
- **P3 is a materialized, mounted projection (D40):** a real GCS directory tree — snapshot
  rebuild + pointer swap, derived from Postgres (placement hints + entities/relations),
  leaf **stubs** with frontmatter (`doc_id`, `artifact_uri`, `content_hash`, section path)
  pointing into the artifacts bucket, generated `_index.md` / `llms.txt` at every level,
  cross-links to K (link, never structural dependency).
- **Filesystem-first consumption is decided (D51):** four read-only mounts, the precedence
  rule ("prefer the filesystem for everything a filesystem can do"), full mount/API parity,
  and a shipped consumption skill. The retrieval design additionally *requires* `_index.md`
  to surface freshness metadata (snapshot version; K-page staleness/flags) — index files
  already have one bound duty.
- **Two newer facts sharpen the picture:** documents are now **lineages with versions**
  (D55) — a "file" in the tree is the lineage at its current version, which is exactly what
  keeps a living document's path stable while its content updates; and design-review **F6**
  (open) already argues path stability must be a published contract: stable ID-addressed
  entity/document leaves + freely reorganizable topic views.

So the original vision — PageIndex summaries + virtual placement + assembled hierarchy mounted
to agents, emails in one place and papers in another — **is the standing design**. The open
questions are below.

## 2. Materialized tree vs. "virtual indexes only" — keep the tree

The alternative considered: skip per-file stubs, publish only per-directory index files that
point at the ID-addressed artifacts. Evaluation:

| | Materialized tree (D40) | Index-files-only |
|---|---|---|
| `ls`/`grep` affordance | full — files appear *as files*; `grep -r` over stubs finds documents by title/summary/entities with zero API calls | broken — nothing to list or grep but the indexes themselves |
| Stable per-document paths (F6) | natural — the stub *is* the stable leaf | gone — documents have no path, only index entries |
| Harness ergonomics | matches how harnesses actually work (open the thing you found) | every hit needs a manual indirection to an opaque artifact URI |
| Build cost | ~1 small object per document per view + index files | index files only |

The only thing index-only saves is writing the stubs — and a stub is a few hundred bytes of
generated frontmatter. Illustrative math at 1M documents: ~1M Class-A GCS writes ≈ **$5 per
full view rebuild**, minutes of wall-clock with parallel writers. That is not a cost worth
trading the primary affordance for. **Recommendation: keep D40's materialized tree; record
index-only as the rejected alternative** (it re-appears every time someone worries about
object counts — the number above is the answer).

## 3. The `_index.md` content contract (the core recommendation)

The design says index files exist but not what is *in* them. Bind it, using the K plane's
two-band pattern (`k_layers_design.md` §5) — deterministic where a machine is exact, LLM only
where synthesis earns its cost:

**Deterministic band — always present, zero LLM, assembled from Postgres:**

1. **Directory identity line** — templated from the taxonomy: *"Emails — client
   correspondence, 2024–2026 · 1,284 documents in 14 subfolders."* Counts, time range,
   source facets: all SQL.
2. **The member table — one row per child, carrying each document's PageIndex root
   summary.** This is the load-bearing idea and it is **free**: the per-file top-level
   summary already exists in `document_sections` (root node, D39); the index file just
   surfaces it next to the filename, plus date/source and entity links. An agent reads *one*
   `_index.md` and knows what every file in the directory is about — without opening any of
   them. That single property converts navigation cost from O(files opened) to O(index files
   read).
3. **Cross-links**: the K pages covering this territory (entity/topic pages), sibling views
   of the same documents (§4), parent/child indexes.
4. **Freshness/flags** (already required by the retrieval design): snapshot version; for
   linked K pages, staleness and open-flag counts.

**Directory-level LLM summaries — considered and REJECTED.** Three options were weighed:
*(a)* nothing beyond the template; *(b)* link the covering K page; *(c)* a dedicated
per-directory micro-summary (small model, membership-hash-debounced, PG-stored). The
decision is **(a) + (b), never (c)**, for three reasons: the member table already carries
the directory's meaning (an LLM band would summarize summaries the reader can see);
where a community/topic/entity K page exists, the directory-level understanding **already
exists, citation-maintained and rot-proof** — a P3-local summary would be a second
understanding layer that drifts (exactly what `e0_files_design.md` §4 warns about: the
global picture is K's job); and rejecting (c) keeps the P3 builder fully deterministic and
zero-LLM (no new D52 ledger surface). The corollary rule: **wanting a directory synthesis
is a signal to create the covering K page** (an ordinary planner decision) — the layer
built to maintain synthesis — never to bolt one onto the projection.

## 4. Taxonomy, multiple views, and fan-out — three structure rules

1. **The top level is configured, not emergent.** Placement hints reconcile *within* a
   skeleton the deployment declares (registry-style config): e.g. `by-type/` (emails, papers,
   contracts, notes…), `by-source/`, `by-topic/` (placement-hint/registry-derived), `entities/`,
   `by-time/`. Emergent top levels reshuffle as the corpus grows — precisely what agents (and
   F6) cannot tolerate. Facets are stable; their *interiors* reorganize freely.
2. **One document, many views — by stub duplication.** An email about Project Atlas belongs
   under `by-type/emails/…` *and* `by-project/atlas/…`. gcsfuse has no real symlinks; stubs
   are cheap generated pointers, so duplication is the mechanism (it is a projection —
   nothing to keep consistent by hand). One **canonical stable path** per document
   (ID-addressed, per F6) is the durable citation target; view paths are documented as
   reorganizable. This closes F6 as a side effect and should land with it.
3. **Bounded fan-out.** A 10,000-entry directory is unbrowsable for an agent and slow to
   list through gcsfuse. Rule: directories shard deterministically (by date, alpha, source)
   above ~100–200 entries — a starting point to measure against real listing behavior. The
   member table (§3.2) makes even sharded levels cheap to traverse: each shard's index
   summarizes its contents.

## 5. Operational cost — the build stays cheap, and mostly deterministic

- **Full rebuild at 1M docs:** stubs (≈$5/view in write ops) + index files (1–5% of object
  count) + `llms.txt`; deterministic assembly from `document_sections` + placement +
  entities/relations — **zero LLM, unconditionally** (§3 rejected the only candidate LLM
  step; the p3 worker keeps its deterministic classification in `workers.md`). Wall-clock
  is parallel small writes — minutes.
- **Cadence:** navigation does not need freshness — P3 at 6-hourly/daily is plenty (D12:
  the cadence *is* the SLA), which caps rebuild cost at single-digit dollars/day even fully
  naive. D40 already permits **incremental tree maintenance as an internal optimization**
  producing the same validated snapshot: with stable leaves (F6), the delta is "stubs for
  changed lineages + regenerate affected `_index.md` files" — small by construction. The
  D55 lineage model helps here too: a living document's update touches its stub's content,
  not its path.

## 6. What "efficient for the agent" means, measurably

The navigation ladder this design produces — each step one `cat`:

```
llms.txt (root orientation)
  → facet _index.md   (what kinds of things exist)
    → directory _index.md  (member table: every file's one-line meaning)
      → stub  (doc orientation + artifact pointer + entity links)
        → document.md  (+ pageindex.json for section-level entry)
```

Properties worth stating as goals: **hops-to-target** (≤4 from cold for a known-shape query)
and **tokens-read-to-target** (bounded by index files, not by opening candidate documents —
the §3.2 member table is what buys this). `grep -r` over stubs gives content-ish search with
zero API calls; anything semantic escalates to the API per the D51 precedence rule. The
consumption skill should teach exactly this ladder, and the navigation behavior belongs in
the eval battery (an S58-style scenario: *find the document answering X using only
`ls`/`cat`/`grep` on the mount* — pass/fail per skill revision).

## 7. Recommendations (where each should land)

1. **Keep the materialized tree; record index-only as rejected** — `e0_files_design.md` §6
   (one paragraph, the §2 table's rationale).
2. **Bind the `_index.md` content contract** (deterministic band of §3, incl. the member
   table with PageIndex root summaries and the freshness/flags duty) — `e0_files_design.md`
   §6; it is currently one vague line ("what's here, summaries, links").
3. **Directory-summary policy**: templated identity line + K-page links; per-directory LLM
   summaries **rejected** (recorded in `e0_files_design.md` §6) — wanting one is the signal
   to create the covering K page.
4. **Structure rules** (configured facets, multi-view stub duplication + one canonical
   stable path, fan-out sharding) — `e0_files_design.md` §6, landing **F6** at the same
   time.
5. **Navigation ladder + metrics + eval scenario** — the consumption skill (retrieval design
   §8) and the eval surface.

Nothing here changes an existing decision — D39/D40/D51 all stand; this fills in the level
of detail below them that the original one-liners left open.

## References

Designs: `e0_files_design.md` §4–§6 (D39/D40), `retrieval_design.md` §7–§8 (D51),
`k_layers_design.md` §5 (two-band pattern, `inputs_hash`), `evidence_lifecycle_design.md`
(D55 lineages). Decisions: D39, D40, D45, D51, D52, D55. Open finding: F6
(`design_review_2026_07.md`). Worker inventory: `workers.md` §6.4.
